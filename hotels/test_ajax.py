from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, Permission
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db
HEADERS = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def user():
    user = User.objects.create_user("ajax_customer", "ajax@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def inventory():
    hotel = Hotel.objects.create(name="Garden Hotel", city="Warsaw", address="Test Street", stars=4)
    room = Room.objects.create(
        hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night=Decimal("200.00")
    )
    hidden = Hotel.objects.create(
        name="Closed Hotel", city="Warsaw", address="Hidden Street", stars=3, is_active=False
    )
    Room.objects.create(
        hotel=hidden, number="999", room_type="SINGLE", capacity=1, price_per_night=Decimal("50.00")
    )
    Room.objects.create(
        hotel=hotel,
        number="888",
        room_type="SINGLE",
        capacity=1,
        price_per_night=Decimal("50.00"),
        is_active=False,
    )
    return hotel, room


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_ajax_anonymous_users_redirect_to_login(client, endpoint):
    response = client.get(reverse(f"hotels:{endpoint}"), **HEADERS)
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_ajax_requires_permission_not_only_header(client, user, endpoint):
    user.groups.clear()
    client.force_login(user)
    assert client.get(reverse(f"hotels:{endpoint}"), **HEADERS).status_code == 403


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
@pytest.mark.parametrize("role", ["CUSTOMER", "MANAGER", "ADMIN"])
def test_ajax_roles_receive_fragment_matching_full_page(client, user, inventory, endpoint, role):
    user.groups.set([Group.objects.get(name=role)])
    client.force_login(user)
    path = reverse(f"hotels:{endpoint}")
    full = client.get(path)
    response = client.get(path, **HEADERS)
    data = response.json()
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert data["count"] == 1 and data["page"] == 1 and data["valid"] is True
    assert data["html"] in full.content.decode()
    assert "<html" not in data["html"] and "<script" not in data["html"]
    assert "Closed Hotel" not in data["html"] and ">888<" not in data["html"]
    assert "X-Requested-With" in response["Vary"] and "X-Requested-With" in full["Vary"]
    assert b"catalogue.js" in full.content and b"data-async-catalogue" in full.content


@pytest.mark.parametrize(
    "endpoint,filters", [("hotel_list", {"city": "Krakow"}), ("room_list", {"min_price": "300"})]
)
def test_ajax_filters_empty_results(client, user, inventory, endpoint, filters):
    client.force_login(user)
    data = client.get(reverse(f"hotels:{endpoint}"), filters, **HEADERS).json()
    assert data["count"] == 0 and data["valid"] is True
    assert "No hotels match" in data["html"] or "No rooms match" in data["html"]


@pytest.mark.parametrize(
    "endpoint,filters,error",
    [
        ("hotel_list", {"stars": "9"}, "Select a valid choice"),
        ("hotel_list", {"name": "x" * 151}, "at most 150"),
        ("room_list", {"capacity": "0"}, "greater than or equal to 1"),
        ("room_list", {"min_price": "300", "max_price": "100"}, "Maximum price must"),
        ("room_list", {"check_in": "invalid"}, "Enter a valid date"),
    ],
)
def test_ajax_invalid_filters_show_server_errors_without_records(
    client, user, inventory, endpoint, filters, error
):
    client.force_login(user)
    data = client.get(reverse(f"hotels:{endpoint}"), filters, **HEADERS).json()
    assert data["valid"] is False and data["count"] == 0
    assert error in data["html"] and "Correct the filters" in data["html"]
    assert "aria-invalid" in data["html"]


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_ajax_pagination_retains_filters_and_stable_order(client, user, inventory, endpoint):
    hotel, room = inventory
    for index in range(12):
        if endpoint == "hotel_list":
            Hotel.objects.create(
                name=f"Garden {index:02}", city="Warsaw", address="Test Street", stars=4
            )
        else:
            Room.objects.create(
                hotel=hotel,
                number=f"2{index:02}",
                room_type="DOUBLE",
                capacity=2,
                price_per_night="200.00",
            )
    client.force_login(user)
    filters = {"city": "Warsaw"} if endpoint == "hotel_list" else {"room_type": "DOUBLE"}
    path = reverse(f"hotels:{endpoint}")
    first = client.get(path, filters, **HEADERS).json()
    second = client.get(path, {**filters, "page": "2"}, **HEADERS).json()
    assert first["count"] == 13 and first["page"] == 1 and second["page"] == 2
    assert "Next</a>" in first["html"] and "Previous</a>" in second["html"]
    assert "page=2" in first["html"]
    assert "city=Warsaw" in first["html"] or "room_type=DOUBLE" in first["html"]
    assert first == client.get(path, filters, **HEADERS).json()


@pytest.mark.parametrize("page,expected", [("nonsense", 1), ("999", 1), ("-1", 1)])
def test_ajax_invalid_page_uses_existing_paginator_rules(client, user, inventory, page, expected):
    client.force_login(user)
    data = client.get(reverse("hotels:hotel_list"), {"page": page}, **HEADERS).json()
    assert data["page"] == expected and data["count"] == 1


def test_ajax_availability_uses_same_booking_rules_and_keeps_dates(client, user, inventory):
    hotel, room = inventory
    start = timezone.localdate() + timedelta(days=5)
    end = start + timedelta(days=2)
    booking = Booking.objects.create(
        user=user,
        room=room,
        check_in=start,
        check_out=end,
        guests_count=1,
        price_per_night_snapshot="200.00",
        total_price="400.00",
    )
    client.force_login(user)
    path = reverse("hotels:room_list")
    filters = {"check_in": start.isoformat(), "check_out": end.isoformat()}
    assert client.get(path, filters, **HEADERS).json()["count"] == 0
    booking.status = Booking.Status.CANCELLED
    booking.save()
    data = client.get(path, filters, **HEADERS).json()
    assert data["count"] == 1
    assert f"check_in={start.isoformat()}&amp;check_out={end.isoformat()}" in data["html"]


def test_ajax_database_and_filter_values_are_html_escaped(client, user, inventory):
    hotel, room = inventory
    hotel.name = '<script>alert("test")</script>'
    hotel.save()
    client.force_login(user)
    data = client.get(reverse("hotels:hotel_list"), {"name": "<script>"}, **HEADERS).json()
    assert data["count"] == 1
    assert "&lt;script&gt;" in data["html"] and "<script>" not in data["html"]


def test_ajax_read_only_user_does_not_receive_booking_action(client, user, inventory):
    user.groups.clear()
    user.user_permissions.add(
        Permission.objects.get(codename="view_room", content_type__app_label="hotels")
    )
    client.force_login(user)
    data = client.get(reverse("hotels:room_list"), **HEADERS).json()
    assert data["count"] == 1 and "Book room" not in data["html"]


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_ajax_header_cannot_enable_mutation(client, user, inventory, endpoint):
    client.force_login(user)
    assert client.post(reverse(f"hotels:{endpoint}"), **HEADERS).status_code == 405
    assert Hotel.objects.count() == 2 and Room.objects.count() == 3


def test_full_page_and_manager_keep_non_javascript_workflow(client, user, inventory):
    user.groups.set([Group.objects.get(name="MANAGER")])
    client.force_login(user)
    full = client.get(reverse("hotels:hotel_list"), {"city": "Warsaw"})
    assert b'method="get"' in full.content and b"city" in full.content
    assert b"Skip to content" in full.content
    response = client.get(reverse("hotels:manage_hotels"))
    assert (
        b"catalogue.js" not in response.content and b"data-async-catalogue" not in response.content
    )
