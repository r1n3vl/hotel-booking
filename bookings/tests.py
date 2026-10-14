import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.forms import BookingForm
from bookings.models import Booking
from bookings.services import change_booking_status, create_booking
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user("booking_customer", "booking@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def other():
    user = User.objects.create_user("other_customer", "other_booking@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def manager():
    user = User.objects.create_user("booking_manager", "manager_booking@example.test")
    user.groups.add(Group.objects.get(name="MANAGER"))
    return user


@pytest.fixture
def room():
    hotel = Hotel.objects.create(
        name="Booking Hotel", city="Warsaw", address="Booking Street 1", stars=3
    )
    return Room.objects.create(
        hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night=Decimal("200.25")
    )


@pytest.fixture
def stay():
    return {
        "check_in": timezone.localdate() + timedelta(days=10),
        "check_out": timezone.localdate() + timedelta(days=13),
        "guests_count": 2,
    }


def request_booking(customer, room, stay):
    return create_booking(actor=customer, room_id=room.pk, **stay)


def legacy_booking(customer, room, start, end, status="PENDING"):
    return Booking.objects.create(
        user=customer,
        room=room,
        check_in=start,
        check_out=end,
        guests_count=1,
        price_per_night_snapshot=room.price_per_night,
        total_price=room.price_per_night * (end - start).days,
        status=status,
    )


def test_create_uses_current_database_price_owner_and_pending_status(customer, room, stay):
    room.price_per_night = Decimal("0.01")
    booking = request_booking(customer, room, stay)
    booking.refresh_from_db()
    assert booking.user_id == customer.pk and booking.room_id == room.pk
    assert booking.status == Booking.Status.PENDING
    assert booking.price_per_night_snapshot == Decimal("200.25")
    assert booking.total_price == Decimal("600.75")
    assert booking.guests_count == 2


def test_snapshot_does_not_change_when_room_price_changes(customer, room, stay):
    booking = request_booking(customer, room, stay)
    Room.objects.filter(pk=room.pk).update(price_per_night="999.99")
    booking.refresh_from_db()
    assert booking.price_per_night_snapshot == Decimal("200.25")
    assert booking.total_price == Decimal("600.75")


@pytest.mark.parametrize("role", ["CUSTOMER", "MANAGER", "ADMIN"])
def test_each_role_creates_its_own_booking(customer, room, stay, role):
    customer.groups.set([Group.objects.get(name=role)])
    assert request_booking(customer, room, stay).user_id == customer.pk


def test_active_superuser_can_book_without_group(room, stay):
    user = User.objects.create_superuser(
        "booking_root", "booking_root@example.test", "Test-only-booking-719!"
    )
    assert create_booking(actor=user, room_id=room.pk, **stay).user_id == user.pk


@pytest.mark.parametrize("actor_type", ["anonymous", "inactive", "no_permission"])
def test_service_rechecks_create_permission(customer, room, stay, actor_type):
    if actor_type == "anonymous":
        actor = AnonymousUser()
    elif actor_type == "inactive":
        customer.is_active = False
        customer.save(update_fields=["is_active"])
        actor = customer
    else:
        customer.groups.clear()
        actor = customer
    with pytest.raises(PermissionDenied):
        create_booking(actor=actor, room_id=room.pk, **stay)
    assert not Booking.objects.exists()


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("check_in", -1, "check_in"),
        ("check_out", 10, "check_out"),
        ("check_out", 9, "check_out"),
        ("guests_count", 0, "guests_count"),
        ("guests_count", 3, "guests_count"),
    ],
)
def test_service_validates_domain_data(customer, room, stay, field, value, error):
    stay[field] = (
        timezone.localdate() + timedelta(days=value) if field.startswith("check_") else value
    )
    with pytest.raises(ValidationError) as captured:
        request_booking(customer, room, stay)
    assert error in captured.value.message_dict
    assert not Booking.objects.exists()


def test_check_in_today_is_allowed(customer, room):
    today = timezone.localdate()
    booking = create_booking(
        actor=customer,
        room_id=room.pk,
        check_in=today,
        check_out=today + timedelta(days=1),
        guests_count=1,
    )
    assert booking.total_price == Decimal("200.25")


@pytest.mark.parametrize("target", ["room", "hotel"])
def test_service_rechecks_active_flags(customer, room, stay, target):
    model, pk = (Room, room.pk) if target == "room" else (Hotel, room.hotel_id)
    model.objects.filter(pk=pk).update(is_active=False)
    with pytest.raises(ValidationError, match="no longer active"):
        request_booking(customer, room, stay)
    assert not Booking.objects.exists()


def test_missing_room_is_validation_error(customer, stay):
    with pytest.raises(ValidationError, match="no longer available"):
        create_booking(actor=customer, room_id=999999, **stay)


def test_total_overflow_is_validation_error_and_rolls_back(customer, room, stay):
    Room.objects.filter(pk=room.pk).update(price_per_night="99999999.99")
    stay["check_out"] = stay["check_in"] + timedelta(days=101)
    with pytest.raises(ValidationError) as captured:
        request_booking(customer, room, stay)
    assert "total_price" in captured.value.message_dict
    assert not Booking.objects.exists()


@pytest.mark.parametrize(
    "status,blocked", [("PENDING", True), ("CONFIRMED", True), ("CANCELLED", False)]
)
@pytest.mark.parametrize(
    "start,end,overlap",
    [
        (10, 13, True),
        (9, 14, True),
        (11, 12, True),
        (8, 11, True),
        (12, 15, True),
        (8, 10, False),
        (13, 15, False),
    ],
)
def test_creation_overlap_rules(customer, room, status, blocked, start, end, overlap):
    today = timezone.localdate()
    legacy_booking(customer, room, today + timedelta(days=10), today + timedelta(days=13), status)
    parameters = {
        "check_in": today + timedelta(days=start),
        "check_out": today + timedelta(days=end),
        "guests_count": 1,
    }
    if blocked and overlap:
        with pytest.raises(ValidationError, match="already booked"):
            request_booking(customer, room, parameters)
        assert Booking.objects.count() == 1
    else:
        request_booking(customer, room, parameters)
        assert Booking.objects.count() == 2


def test_bookings_for_other_rooms_do_not_block(customer, room, stay):
    other_room = Room.objects.create(
        hotel=room.hotel,
        number="102",
        room_type="DOUBLE",
        capacity=2,
        price_per_night=room.price_per_night,
    )
    request_booking(customer, other_room, stay)
    assert request_booking(customer, room, stay).room_id == room.pk


def test_form_validates_and_has_only_public_fields(room, stay):
    form = BookingForm(stay, room=room)
    assert form.is_valid(), form.errors
    assert set(form.fields) == {"check_in", "check_out", "guests_count"}
    assert form.cleaned_data == stay
    assert form.fields["guests_count"].widget.attrs["max"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("check_in", ""),
        ("check_out", ""),
        ("guests_count", ""),
        ("check_in", "not-a-date"),
        ("check_out", "2026-02-30"),
        ("guests_count", "two"),
        ("guests_count", "1.5"),
        ("guests_count", "-1"),
        ("guests_count", "0"),
        ("guests_count", "3"),
        ("guests_count", "32768"),
    ],
)
def test_invalid_form_data_cannot_create_booking(client, customer, room, stay, field, value):
    client.force_login(customer)
    stay[field] = value
    response = client.post(reverse("bookings:create", kwargs={"room_id": room.pk}), stay)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Booking.objects.exists()


def test_view_creates_and_redirects_to_private_result_ignoring_forged_fields(
    client, customer, other, room, stay
):
    client.force_login(customer)
    payload = {
        **stay,
        "user": other.pk,
        "user_id": other.pk,
        "room": 999999,
        "room_id": 999999,
        "price_per_night_snapshot": "0.01",
        "total_price": "0.03",
        "status": "CONFIRMED",
        "next": "https://attacker.example/",
    }
    response = client.post(reverse("bookings:create", kwargs={"room_id": room.pk}), payload)
    booking = Booking.objects.get()
    assert response.status_code == 302 and response.url == reverse(
        "bookings:detail", kwargs={"pk": booking.pk}
    )
    assert booking.user_id == customer.pk and booking.room_id == room.pk
    assert booking.status == "PENDING" and booking.total_price == Decimal("600.75")
    result = client.get(response.url)
    assert result.status_code == 200 and result.context["nights"] == 3
    assert b"600.75 PLN" in result.content and b"awaiting confirmation" in result.content
    client.get(response.url)
    assert Booking.objects.count() == 1


def test_form_get_prefills_catalogue_dates_without_writing(client, customer, room, stay):
    client.force_login(customer)
    response = client.get(
        reverse("bookings:create", kwargs={"room_id": room.pk}),
        {**stay, "guests_count": 999, "status": "CONFIRMED"},
    )
    assert response.status_code == 200
    assert response.context["form"]["check_in"].value() == str(stay["check_in"])
    assert response.context["form"]["check_out"].value() == str(stay["check_out"])
    assert response.context["form"]["guests_count"].value() == 1
    assert not Booking.objects.exists()


def test_duplicate_submission_returns_form_error(client, customer, room, stay):
    client.force_login(customer)
    path = reverse("bookings:create", kwargs={"room_id": room.pk})
    assert client.post(path, stay).status_code == 302
    response = client.post(path, stay)
    assert response.status_code == 200
    assert "already booked" in response.context["form"].non_field_errors()[0]
    assert Booking.objects.count() == 1


def test_room_changed_after_form_validation_is_rechecked(client, customer, room, stay):
    client.force_login(customer)
    original = create_booking

    def update_then_create(**kwargs):
        Room.objects.filter(pk=room.pk).update(capacity=1)
        return original(**kwargs)

    with patch("bookings.views.create_booking", side_effect=update_then_create):
        response = client.post(reverse("bookings:create", kwargs={"room_id": room.pk}), stay)
    assert response.status_code == 200 and response.context["form"].non_field_errors()
    assert not Booking.objects.exists()


@pytest.mark.parametrize("target", ["room", "hotel", "missing"])
def test_view_returns_404_for_unavailable_room(client, customer, room, stay, target):
    client.force_login(customer)
    pk = room.pk
    if target == "missing":
        pk = 999999
    elif target == "room":
        Room.objects.filter(pk=pk).update(is_active=False)
    else:
        Hotel.objects.filter(pk=room.hotel_id).update(is_active=False)
    path = reverse("bookings:create", kwargs={"room_id": pk})
    assert client.get(path).status_code == 404
    assert client.post(path, stay).status_code == 404
    assert not Booking.objects.exists()


def test_anonymous_and_unprivileged_creation_is_blocked(client, customer, room, stay):
    path = reverse("bookings:create", kwargs={"room_id": room.pk})
    assert client.get(path).status_code == 302
    assert client.post(path, stay).status_code == 302
    customer.groups.clear()
    client.force_login(customer)
    assert client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code == 403
    assert client.post(path, stay).status_code == 403
    assert not Booking.objects.exists()


def test_detail_enforces_ownership_and_manager_access(client, customer, other, manager, room, stay):
    booking = request_booking(customer, room, stay)
    path = reverse("bookings:detail", kwargs={"pk": booking.pk})
    assert client.get(path).status_code == 302
    client.force_login(other)
    assert client.get(path).status_code == 404
    assert client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code == 404
    client.force_login(manager)
    assert client.get(path).status_code == 200
    client.force_login(customer)
    assert client.get(path).status_code == 200
    assert client.post(path).status_code == 405
    assert client.get(reverse("bookings:detail", kwargs={"pk": 999999})).status_code == 404
    customer.groups.clear()
    assert client.get(path).status_code == 403


def test_create_rejects_unsupported_methods(client, customer, room):
    client.force_login(customer)
    assert client.put(reverse("bookings:create", kwargs={"room_id": room.pk})).status_code == 405


def test_creation_requires_real_csrf_token(customer, room, stay):
    client = Client(enforce_csrf_checks=True)
    client.force_login(customer)
    path = reverse("bookings:create", kwargs={"room_id": room.pk})
    assert client.post(path, stay).status_code == 403
    assert client.post(path, stay, HTTP_X_CSRFTOKEN="wrong").status_code == 403
    assert not Booking.objects.exists()
    client.get(path)
    token = client.cookies["csrftoken"].value
    assert client.post(path, stay, HTTP_X_CSRFTOKEN=token).status_code == 302
    assert Booking.objects.count() == 1


def test_room_catalogue_links_keep_valid_dates_and_follow_permission(client, customer, room, stay):
    client.force_login(customer)
    response = client.get(
        reverse("hotels:room_list"), {"check_in": stay["check_in"], "check_out": stay["check_out"]}
    )
    html = response.content.decode()
    assert (
        f"/rooms/{room.pk}/book/?check_in={stay['check_in']}&amp;check_out={stay['check_out']}"
        in html
    )
    customer.groups.clear()
    customer.user_permissions.add(Permission.objects.get(codename="view_room"))
    response = client.get(reverse("hotels:room_list"))
    assert b"Book room" not in response.content


def test_booking_templates_escape_record_text(client, customer, room, stay):
    client.force_login(customer)
    Hotel.objects.filter(pk=room.hotel_id).update(name="<script>alert(1)</script>")
    response = client.get(reverse("bookings:create", kwargs={"room_id": room.pk}))
    assert b"&lt;script&gt;" in response.content and b"<script>" not in response.content
    booking = request_booking(customer, room, stay)
    response = client.get(reverse("bookings:detail", kwargs={"pk": booking.pk}))
    assert b"&lt;script&gt;" in response.content and b"<script>" not in response.content


def test_manager_confirms_pending_booking(customer, manager, room, stay):
    booking = request_booking(customer, room, stay)
    confirmed = change_booking_status(actor=manager, booking_id=booking.pk, status="CONFIRMED")
    confirmed.refresh_from_db()
    assert confirmed.status == "CONFIRMED" and confirmed.total_price == Decimal("600.75")


def test_customer_cannot_confirm_or_cancel_another_users_booking(customer, other, room, stay):
    booking = request_booking(customer, room, stay)
    with pytest.raises(PermissionDenied):
        change_booking_status(actor=customer, booking_id=booking.pk, status="CONFIRMED")
    with pytest.raises(PermissionDenied):
        change_booking_status(actor=other, booking_id=booking.pk, status="CANCELLED")
    booking.refresh_from_db()
    assert booking.status == "PENDING"


@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_owner_can_cancel_future_stay_and_release_availability(customer, room, stay, status):
    booking = legacy_booking(customer, room, stay["check_in"], stay["check_out"], status)
    cancelled = change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED")
    assert cancelled.status == "CANCELLED"
    assert request_booking(customer, room, stay).status == "PENDING"


def test_cancel_is_idempotent_and_cancelled_is_terminal(customer, manager, room, stay):
    booking = request_booking(customer, room, stay)
    first = change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED")
    repeated = change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED")
    assert first.updated_at == repeated.updated_at and repeated.status == "CANCELLED"
    with pytest.raises(ValidationError, match="Only pending"):
        change_booking_status(actor=manager, booking_id=booking.pk, status="CONFIRMED")


@pytest.mark.parametrize("offset", [0, -1])
@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_customer_cannot_cancel_started_stay(customer, room, offset, status):
    start = timezone.localdate() + timedelta(days=offset)
    booking = legacy_booking(customer, room, start, start + timedelta(days=3), status)
    with pytest.raises(ValidationError, match="already started"):
        change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED")


def test_manager_can_clean_stale_pending_but_not_cancel_started_confirmed(customer, manager, room):
    yesterday = timezone.localdate() - timedelta(days=1)
    booking = legacy_booking(customer, room, yesterday, yesterday + timedelta(days=3))
    with pytest.raises(ValidationError, match="past stay"):
        change_booking_status(actor=manager, booking_id=booking.pk, status="CONFIRMED")
    assert (
        change_booking_status(actor=manager, booking_id=booking.pk, status="CANCELLED").status
        == "CANCELLED"
    )
    confirmed = legacy_booking(
        customer, room, yesterday, yesterday + timedelta(days=3), "CONFIRMED"
    )
    with pytest.raises(ValidationError, match="already started"):
        change_booking_status(actor=manager, booking_id=confirmed.pk, status="CANCELLED")


def test_confirm_today_allowed_and_confirm_again_rejected(customer, manager, room):
    today = timezone.localdate()
    booking = legacy_booking(customer, room, today, today + timedelta(days=2))
    change_booking_status(actor=manager, booking_id=booking.pk, status="CONFIRMED")
    with pytest.raises(ValidationError, match="Only pending"):
        change_booking_status(actor=manager, booking_id=booking.pk, status="CONFIRMED")


def test_transition_validation_and_inactive_actor(customer, room, stay):
    booking = request_booking(customer, room, stay)
    for actor in (AnonymousUser(),):
        with pytest.raises(PermissionDenied):
            change_booking_status(actor=actor, booking_id=booking.pk, status="CANCELLED")
    customer.is_active = False
    customer.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED")
    customer.is_active = True
    for status in ("PENDING", "UNKNOWN"):
        with pytest.raises(ValidationError, match="Choose Confirmed or Cancelled"):
            change_booking_status(actor=customer, booking_id=booking.pk, status=status)
    with pytest.raises(ValidationError, match="not found"):
        change_booking_status(actor=customer, booking_id=999999, status="CANCELLED")


def test_cancellation_still_works_for_inactive_room_and_hotel(customer, room, stay):
    booking = request_booking(customer, room, stay)
    Room.objects.filter(pk=room.pk).update(is_active=False)
    Hotel.objects.filter(pk=room.hotel_id).update(is_active=False)
    assert (
        change_booking_status(actor=customer, booking_id=booking.pk, status="CANCELLED").status
        == "CANCELLED"
    )


def wait_for_postgresql_lock(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            row = cursor.fetchone()
        if row and row[0] == "Lock":
            return True
        time.sleep(0.02)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first_action", ["create", "cancel"])
def test_concurrent_creation_waits_for_room_lock_and_rechecks_committed_state(
    customer, room, stay, first_action
):
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_")
    existing = request_booking(customer, room, stay) if first_action == "cancel" else None
    locked = threading.Event()
    release = threading.Event()
    pids = queue.Queue()

    def first_worker():
        close_old_connections()
        try:
            actor = User.objects.get(pk=customer.pk)
            with transaction.atomic():
                Room.objects.select_for_update().get(pk=room.pk)
                locked.set()
                if not release.wait(10):
                    raise TimeoutError("Room lock release timed out")
                if first_action == "create":
                    return create_booking(actor=actor, room_id=room.pk, **stay).pk
                return change_booking_status(
                    actor=actor, booking_id=existing.pk, status="CANCELLED"
                ).pk
        finally:
            connections["default"].close()

    def second_worker():
        close_old_connections()
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            actor = User.objects.get(pk=customer.pk)
            try:
                return "created", create_booking(actor=actor, room_id=room.pk, **stay).pk
            except ValidationError as exc:
                return "rejected", exc.messages
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(first_worker)
        try:
            assert locked.wait(5)
            second = workers.submit(second_worker)
            assert wait_for_postgresql_lock(pids.get(timeout=5))
            assert not second.done()
        finally:
            release.set()
        first.result(timeout=10)
        result, detail = second.result(timeout=10)
    if first_action == "create":
        assert result == "rejected" and "already booked" in detail[0]
        assert Booking.objects.count() == 1
    else:
        assert result == "created"
        assert Booking.objects.get(pk=existing.pk).status == "CANCELLED"
        assert Booking.objects.filter(status="PENDING").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_adjacent_stays_both_succeed(customer, room, stay):
    barrier = threading.Barrier(2)

    def worker(start, end):
        close_old_connections()
        try:
            actor = User.objects.get(pk=customer.pk)
            barrier.wait(timeout=5)
            return create_booking(
                actor=actor, room_id=room.pk, check_in=start, check_out=end, guests_count=1
            ).pk
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(worker, stay["check_in"], stay["check_out"])
        second = workers.submit(worker, stay["check_out"], stay["check_out"] + timedelta(days=2))
        assert first.result(timeout=10) != second.result(timeout=10)
    assert Booking.objects.count() == 2
