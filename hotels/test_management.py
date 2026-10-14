import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from bookings.services import create_booking
from hotels.management import locked_room as original_locked_room
from hotels.management_forms import HotelForm, RoomForm
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager():
    user = User.objects.create_user("maintenance_manager", "maintenance@example.test")
    user.groups.add(Group.objects.get(name="MANAGER"))
    return user


@pytest.fixture
def customer():
    user = User.objects.create_user("maintenance_customer", "maintenance_customer@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def hotel():
    return Hotel.objects.create(
        name="Managed Hotel", city="Warsaw", address="Managed Street", stars=3
    )


@pytest.fixture
def room(hotel):
    return Room.objects.create(
        hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night=Decimal("200.00")
    )


def hotel_data(**changes):
    return {
        "name": "Updated Hotel",
        "city": "Krakow",
        "address": "Updated Street",
        "description": "Updated description",
        "stars": 4,
        "is_active": "on",
        **changes,
    }


def room_data(parent, **changes):
    return {
        "hotel": parent.pk,
        "number": "101",
        "room_type": "DOUBLE",
        "capacity": 2,
        "price_per_night": "250.00",
        "description": "Updated room",
        "is_active": "on",
        **changes,
    }


def booked(customer, room, status="PENDING", offset=5):
    start = timezone.localdate() + timedelta(days=offset)
    return Booking.objects.create(
        user=customer,
        room=room,
        check_in=start,
        check_out=start + timedelta(days=2),
        guests_count=2,
        price_per_night_snapshot="200.00",
        total_price="400.00",
        status=status,
    )


@pytest.mark.parametrize(
    "endpoint",
    ["manage_hotels", "manage_rooms", "hotel_create", "room_create", "hotel_update", "room_update"],
)
def test_management_requires_permission(client, customer, hotel, room, endpoint):
    kwargs = (
        {"pk": hotel.pk if endpoint == "hotel_update" else room.pk}
        if endpoint.endswith("update")
        else {}
    )
    url = reverse(f"hotels:{endpoint}", kwargs=kwargs)
    assert client.get(url).status_code == 302
    client.force_login(customer)
    assert client.get(url).status_code == 403
    assert client.post(url, hotel_data()).status_code == 403
    assert client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code == 403


@pytest.mark.parametrize("role", ["MANAGER", "ADMIN"])
def test_managers_and_admins_can_open_every_management_page(client, manager, hotel, room, role):
    manager.groups.set([Group.objects.get(name=role)])
    client.force_login(manager)
    for endpoint, kwargs in [
        ("manage_hotels", {}),
        ("manage_rooms", {}),
        ("hotel_create", {}),
        ("room_create", {}),
        ("hotel_update", {"pk": hotel.pk}),
        ("room_update", {"pk": room.pk}),
    ]:
        assert client.get(reverse(f"hotels:{endpoint}", kwargs=kwargs)).status_code == 200


def test_create_and_update_hotel_preserve_existing_rows(client, manager, hotel):
    client.force_login(manager)
    response = client.post(reverse("hotels:hotel_create"), hotel_data())
    assert response.status_code == 302 and Hotel.objects.count() == 2
    hotel.refresh_from_db()
    assert hotel.name == "Managed Hotel"
    response = client.post(reverse("hotels:hotel_update", kwargs={"pk": hotel.pk}), hotel_data())
    assert response.status_code == 302
    hotel.refresh_from_db()
    assert hotel.name == "Updated Hotel" and hotel.city == "Krakow" and hotel.stars == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", "x" * 151),
        ("city", ""),
        ("address", ""),
        ("stars", 0),
        ("stars", 6),
        ("stars", "bad"),
    ],
)
def test_hotel_form_validation_does_not_write(client, manager, hotel, field, value):
    client.force_login(manager)
    response = client.post(
        reverse("hotels:hotel_update", kwargs={"pk": hotel.pk}), hotel_data(**{field: value})
    )
    assert response.status_code == 200 and response.context["form"].errors
    hotel.refresh_from_db()
    assert hotel.name == "Managed Hotel" and hotel.stars == 3


def test_create_room_and_prevent_duplicates(client, manager, hotel, room):
    client.force_login(manager)
    url = reverse("hotels:room_create")
    response = client.post(url, room_data(hotel, number="102"))
    assert response.status_code == 302 and Room.objects.count() == 2
    duplicate = client.post(url, room_data(hotel, number="102"))
    assert duplicate.status_code == 200 and duplicate.context["form"].errors
    assert Room.objects.count() == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("hotel", 999999),
        ("number", ""),
        ("number", "x" * 21),
        ("room_type", "UNKNOWN"),
        ("capacity", 0),
        ("capacity", -1),
        ("capacity", 32768),
        ("price_per_night", 0),
        ("price_per_night", "-0.01"),
        ("price_per_night", "NaN"),
        ("price_per_night", "100000000.00"),
        ("price_per_night", "1.001"),
    ],
)
def test_room_form_validation_does_not_write(client, manager, hotel, field, value):
    client.force_login(manager)
    response = client.post(reverse("hotels:room_create"), room_data(hotel, **{field: value}))
    assert response.status_code == 200 and response.context["form"].errors
    assert not Room.objects.exists()


def test_room_cannot_move_hotel_and_price_snapshots_are_preserved(
    client, manager, customer, hotel, room
):
    booking = booked(customer, room)
    other = Hotel.objects.create(name="Other Hotel", city="Warsaw", address="Other", stars=4)
    client.force_login(manager)
    response = client.post(
        reverse("hotels:room_update", kwargs={"pk": room.pk}),
        room_data(other, number="103", price_per_night="333.33"),
    )
    assert response.status_code == 302
    room.refresh_from_db()
    booking.refresh_from_db()
    assert (
        room.hotel_id == hotel.pk
        and room.number == "103"
        and room.price_per_night == Decimal("333.33")
    )
    assert booking.price_per_night_snapshot == Decimal("200.00") and booking.total_price == Decimal(
        "400.00"
    )


@pytest.mark.parametrize(
    "status,offset,blocked",
    [
        ("PENDING", 5, True),
        ("CONFIRMED", 5, True),
        ("CONFIRMED", -1, True),
        ("CANCELLED", 5, False),
        ("PENDING", -5, False),
    ],
)
def test_capacity_reduction_protects_ongoing_and_future_bookings(
    client, manager, customer, hotel, room, status, offset, blocked
):
    booked(customer, room, status, offset)
    client.force_login(manager)
    response = client.post(
        reverse("hotels:room_update", kwargs={"pk": room.pk}), room_data(hotel, capacity=1)
    )
    room.refresh_from_db()
    assert response.status_code == (200 if blocked else 302)
    assert room.capacity == (2 if blocked else 1)
    if blocked:
        assert "capacity" in response.context["form"].errors


def test_deactivation_preserves_history_and_hides_public_records(
    client, manager, customer, hotel, room
):
    booking = booked(customer, room)
    client.force_login(manager)
    client.post(
        reverse("hotels:room_update", kwargs={"pk": room.pk}), room_data(hotel, is_active="")
    )
    assert Booking.objects.filter(pk=booking.pk).exists()
    assert client.get(reverse("hotels:room_list")).context["page_obj"].paginator.count == 0
    client.post(reverse("hotels:room_update", kwargs={"pk": room.pk}), room_data(hotel))
    client.post(reverse("hotels:hotel_update", kwargs={"pk": hotel.pk}), hotel_data(is_active=""))
    assert client.get(reverse("hotels:hotel_list")).context["page_obj"].paginator.count == 0
    assert client.get(reverse("hotels:room_list")).context["page_obj"].paginator.count == 0
    assert client.get(reverse("hotels:manage_hotels")).context["page_obj"].paginator.count == 1
    assert client.get(reverse("hotels:manage_rooms")).context["page_obj"].paginator.count == 1
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_management_filters_include_inactive_and_validate(client, manager, hotel, room):
    Hotel.objects.create(name="Hidden", city="Gdansk", address="Street", stars=5, is_active=False)
    Room.objects.create(
        hotel=hotel,
        number="999",
        room_type="SUITE",
        capacity=4,
        price_per_night="500.00",
        is_active=False,
    )
    client.force_login(manager)
    for endpoint, parameters, count in [
        ("manage_hotels", {"active": "inactive"}, 1),
        (
            "manage_hotels",
            {"active": "active", "name": "managed", "city": "WARSAW", "stars": "3"},
            1,
        ),
        (
            "manage_rooms",
            {"active": "inactive", "room_type": "SUITE", "number": "99", "hotel": hotel.pk},
            1,
        ),
        ("manage_rooms", {"active": "active"}, 1),
        ("manage_hotels", {"active": "bad"}, 0),
        ("manage_rooms", {"room_type": "bad"}, 0),
    ]:
        response = client.get(reverse(f"hotels:{endpoint}"), parameters)
        assert response.context["page_obj"].paginator.count == count


def test_management_pagination_keeps_filters(client, manager):
    for i in range(12):
        Hotel.objects.create(name=f"Page {i:02}", city="Warsaw", address="Street", stars=3)
    client.force_login(manager)
    response = client.get(reverse("hotels:manage_hotels"), {"city": "Warsaw", "page": "2"})
    assert len(response.context["page_obj"]) == 2
    assert response.context["filter_query"] == "city=Warsaw"


@pytest.mark.parametrize("endpoint", ["hotel_create", "hotel_update", "room_create", "room_update"])
def test_management_post_requires_csrf(manager, hotel, room, endpoint):
    client = Client(enforce_csrf_checks=True)
    client.force_login(manager)
    kwargs = (
        {"pk": hotel.pk if endpoint == "hotel_update" else room.pk}
        if endpoint.endswith("update")
        else {}
    )
    url = reverse(f"hotels:{endpoint}", kwargs=kwargs)
    data = hotel_data() if endpoint.startswith("hotel") else room_data(hotel, number="new102")
    assert client.post(url, data).status_code == 403
    client.get(url)
    assert (
        client.post(url, data, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code
        == 302
    )


def test_room_integrity_race_is_form_error(client, manager, hotel):
    client.force_login(manager)
    with patch("hotels.management.RoomForm.save", side_effect=IntegrityError("duplicate")):
        response = client.post(reverse("hotels:room_create"), room_data(hotel))
    assert response.status_code == 200 and response.context["form"].non_field_errors()
    assert not Room.objects.exists()


def test_edit_forms_only_expose_expected_fields(hotel, room):
    assert set(HotelForm().fields) == {
        "name",
        "city",
        "address",
        "description",
        "stars",
        "is_active",
    }
    form = RoomForm(instance=room)
    assert form.fields["hotel"].disabled
    assert "created_at" not in form.fields and "updated_at" not in form.fields


def test_unknown_records_and_unsupported_methods(client, manager):
    client.force_login(manager)
    for endpoint in ("hotel_update", "room_update"):
        assert client.get(reverse(f"hotels:{endpoint}", kwargs={"pk": 999999})).status_code == 404
    assert client.put(reverse("hotels:hotel_create")).status_code == 405
    assert client.post(reverse("hotels:manage_rooms")).status_code == 405


@pytest.mark.django_db(transaction=True)
def test_capacity_change_waits_for_concurrent_booking_commit(manager, customer, hotel, room):
    locked = threading.Event()
    release = threading.Event()
    pids = queue.Queue()
    today = timezone.localdate()

    def booking_worker():
        close_old_connections()
        try:
            actor = User.objects.get(pk=customer.pk)
            with transaction.atomic():
                Room.objects.select_for_update().get(pk=room.pk)
                locked.set()
                assert release.wait(10)
                return create_booking(
                    actor=actor,
                    room_id=room.pk,
                    check_in=today + timedelta(days=5),
                    check_out=today + timedelta(days=7),
                    guests_count=2,
                ).pk
        finally:
            connections["default"].close()

    def edit_worker():
        close_old_connections()
        try:
            client = Client()
            client.force_login(User.objects.get(pk=manager.pk))
            return client.post(
                reverse("hotels:room_update", kwargs={"pk": room.pk}), room_data(hotel, capacity=1)
            ).status_code
        finally:
            connections["default"].close()

    def observe_room_lock(pk):
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        return original_locked_room(pk)

    with (
        patch("hotels.management.locked_room", side_effect=observe_room_lock),
        ThreadPoolExecutor(max_workers=2) as workers,
    ):
        first = workers.submit(booking_worker)
        try:
            assert locked.wait(5)
            second = workers.submit(edit_worker)
            pid = pids.get(timeout=5)
            waiting = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid]
                    )
                    row = cursor.fetchone()
                if row and row[0] == "Lock":
                    waiting = True
                    break
                time.sleep(0.02)
            assert waiting and not second.done()
        finally:
            release.set()
        first.result(timeout=10)
        assert second.result(timeout=10) == 200
    room.refresh_from_db()
    assert room.capacity == 2 and Booking.objects.count() == 1
    assert connection.vendor == "postgresql"
