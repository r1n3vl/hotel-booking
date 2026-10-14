from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user("actions_customer", "actions@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def other():
    user = User.objects.create_user("actions_other", "actions_other@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def manager():
    user = User.objects.create_user("actions_manager", "actions_manager@example.test")
    user.groups.add(Group.objects.get(name="MANAGER"))
    return user


@pytest.fixture
def room():
    hotel = Hotel.objects.create(name="Actions Hotel", city="Warsaw", address="Street", stars=3)
    return Room.objects.create(
        hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night=Decimal("200.00")
    )


def booking_for(user, room, status="PENDING", offset=5):
    start = timezone.localdate() + timedelta(days=offset)
    return Booking.objects.create(
        user=user,
        room=room,
        check_in=start,
        check_out=start + timedelta(days=2),
        guests_count=2,
        price_per_night_snapshot="200.00",
        total_price="400.00",
        status=status,
    )


def ids(response):
    return {record.pk for record in response.context["page_obj"]}


def test_my_bookings_are_owned_even_with_forged_query_parameters(
    client, customer, other, manager, room
):
    own = booking_for(customer, room)
    foreign = booking_for(other, room)
    managed = booking_for(manager, room)
    client.force_login(customer)
    response = client.get(
        reverse("bookings:my_bookings"),
        {"user": other.pk, "all_bookings": "true", "customer": other.username},
    )
    assert ids(response) == {own.pk}
    client.force_login(manager)
    assert ids(client.get(reverse("bookings:my_bookings"))) == {managed.pk}
    assert ids(client.get(reverse("bookings:all_bookings"))) == {own.pk, foreign.pk, managed.pk}


def test_booking_list_access(client, customer, manager):
    for endpoint in ("my_bookings", "all_bookings"):
        assert client.get(reverse(f"bookings:{endpoint}")).status_code == 302
    client.force_login(customer)
    assert client.get(reverse("bookings:my_bookings")).status_code == 200
    assert client.get(reverse("bookings:all_bookings")).status_code == 403
    assert (
        client.get(
            reverse("bookings:all_bookings"), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        ).status_code
        == 403
    )
    customer.groups.clear()
    assert client.get(reverse("bookings:my_bookings")).status_code == 403
    client.force_login(manager)
    assert client.post(reverse("bookings:all_bookings")).status_code == 405


def test_booking_filters_and_pagination(client, customer, room):
    bookings = [booking_for(customer, room, offset=i + 1) for i in range(12)]
    cancelled = booking_for(customer, room, "CANCELLED", 20)
    client.force_login(customer)
    response = client.get(
        reverse("bookings:my_bookings"), {"status": "PENDING", "search": "actions", "page": "2"}
    )
    assert len(ids(response)) == 2 and response.context["page_obj"].paginator.count == 12
    assert (
        "status=PENDING" in response.context["filter_query"]
        and "page=" not in response.context["filter_query"]
    )
    response = client.get(reverse("bookings:my_bookings"), {"status": "CANCELLED", "search": "101"})
    assert ids(response) == {cancelled.pk}
    start = timezone.localdate() + timedelta(days=1)
    assert ids(
        client.get(reverse("bookings:my_bookings"), {"from_date": start, "to_date": start})
    ) == {bookings[0].pk}


@pytest.mark.parametrize(
    "parameters",
    [
        {"status": "UNKNOWN"},
        {"search": "x" * 151},
        {"from_date": "bad"},
        {"from_date": "2027-02-02", "to_date": "2027-02-01"},
    ],
)
def test_invalid_booking_filters_show_errors_and_no_records(client, customer, room, parameters):
    booking_for(customer, room)
    client.force_login(customer)
    response = client.get(reverse("bookings:my_bookings"), parameters)
    assert response.status_code == 200 and response.context["form"].errors
    assert ids(response) == set()


def test_manager_can_filter_by_customer_and_inactive_records_remain_visible(
    client, customer, other, manager, room
):
    own = booking_for(customer, room)
    booking_for(other, room)
    Hotel.objects.filter(pk=room.hotel_id).update(is_active=False)
    Room.objects.filter(pk=room.pk).update(is_active=False)
    client.force_login(manager)
    assert ids(client.get(reverse("bookings:all_bookings"), {"customer": "actions_customer"})) == {
        own.pk
    }
    client.force_login(customer)
    assert ids(client.get(reverse("bookings:my_bookings"))) == {own.pk}


@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_owner_cancel_and_repeat_cancel_are_safe(client, customer, room, status):
    booking = booking_for(customer, room, status)
    client.force_login(customer)
    url = reverse("bookings:cancel", kwargs={"pk": booking.pk})
    assert client.get(url).status_code == 405
    response = client.post(url, {"status": "CONFIRMED", "next": "https://attacker.example/"})
    assert response.url == reverse("bookings:detail", kwargs={"pk": booking.pk})
    booking.refresh_from_db()
    timestamp = booking.updated_at
    assert booking.status == "CANCELLED"
    assert b"Booking cancelled." in client.get(response.url).content
    client.post(url)
    booking.refresh_from_db()
    assert booking.updated_at == timestamp


def test_other_customer_cannot_cancel_or_confirm(client, customer, other, room):
    booking = booking_for(customer, room)
    client.force_login(other)
    assert client.post(reverse("bookings:cancel", kwargs={"pk": booking.pk})).status_code == 404
    assert client.post(reverse("bookings:confirm", kwargs={"pk": booking.pk})).status_code == 403
    booking.refresh_from_db()
    assert booking.status == "PENDING"


def test_manager_confirms_and_cancels_foreign_future_booking(client, customer, manager, room):
    booking = booking_for(customer, room)
    client.force_login(manager)
    confirm = reverse("bookings:confirm", kwargs={"pk": booking.pk})
    assert client.get(confirm).status_code == 405
    response = client.post(confirm)
    booking.refresh_from_db()
    assert booking.status == "CONFIRMED"
    assert b"Booking confirmed." in client.get(response.url).content
    assert client.post(reverse("bookings:cancel", kwargs={"pk": booking.pk})).status_code == 302
    booking.refresh_from_db()
    assert booking.status == "CANCELLED"


@pytest.mark.parametrize("status,offset", [("CONFIRMED", 0), ("PENDING", 0), ("CONFIRMED", -1)])
def test_started_customer_cancellation_shows_error_without_change(
    client, customer, room, status, offset
):
    booking = booking_for(customer, room, status, offset)
    client.force_login(customer)
    response = client.post(reverse("bookings:cancel", kwargs={"pk": booking.pk}))
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == status
    detail = client.get(response.url)
    assert b"already started" in detail.content
    assert b">Cancel booking</button>" not in detail.content


def test_cancelled_booking_cannot_be_confirmed_and_button_visibility_matches_rules(
    client, customer, manager, room
):
    booking = booking_for(customer, room)
    detail = reverse("bookings:detail", kwargs={"pk": booking.pk})
    client.force_login(customer)
    response = client.get(detail)
    assert response.context["can_cancel"] and not response.context["can_confirm"]
    client.force_login(manager)
    response = client.get(detail)
    assert response.context["can_confirm"] and response.context["can_cancel"]
    booking.status = "CANCELLED"
    booking.save(update_fields=["status"])
    response = client.post(reverse("bookings:confirm", kwargs={"pk": booking.pk}))
    detail_response = client.get(response.url)
    assert b"Only pending" in detail_response.content
    assert not detail_response.context["can_cancel"] and not detail_response.context["can_confirm"]


@pytest.mark.parametrize("endpoint", ["cancel", "confirm"])
def test_booking_actions_require_csrf_and_valid_id(manager, customer, room, endpoint):
    booking = booking_for(customer, room)
    client = Client(enforce_csrf_checks=True)
    client.force_login(manager)
    url = reverse(f"bookings:{endpoint}", kwargs={"pk": booking.pk})
    assert client.post(url).status_code == 403
    booking.refresh_from_db()
    assert booking.status == "PENDING"
    client.get(reverse("bookings:detail", kwargs={"pk": booking.pk}))
    assert client.post(url, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code == 302
    assert (
        client.post(
            reverse(f"bookings:{endpoint}", kwargs={"pk": 999999}),
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        ).status_code
        == 404
    )


def test_navigation_and_management_dashboard(client, customer, manager):
    client.force_login(customer)
    html = client.get(reverse("accounts:home")).content.decode()
    assert 'href="/bookings/"' in html and ">Manage</a>" not in html
    client.force_login(manager)
    html = client.get(reverse("accounts:manager_area")).content.decode()
    assert (
        'href="/management/hotels/"' in html
        and 'href="/management/rooms/"' in html
        and 'href="/management/bookings/"' in html
    )
