from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from hotels.forms import HotelFilterForm, RoomFilterForm
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user("catalogue_customer", "catalogue@example.test")
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def catalogue():
    alpha = Hotel.objects.create(
        name="Alpha Hotel", city="Warsaw", address="Main Street 1", stars=3
    )
    beta = Hotel.objects.create(name="Beta Hotel", city="Krakow", address="Main Street 2", stars=5)
    hidden = Hotel.objects.create(
        name="Hidden Hotel", city="Warsaw", address="Closed Street", stars=4, is_active=False
    )
    single = Room.objects.create(
        hotel=alpha, number="101", room_type="SINGLE", capacity=1, price_per_night="100.00"
    )
    double = Room.objects.create(
        hotel=alpha, number="102", room_type="DOUBLE", capacity=2, price_per_night="200.00"
    )
    suite = Room.objects.create(
        hotel=beta, number="201", room_type="SUITE", capacity=4, price_per_night="500.00"
    )
    inactive = Room.objects.create(
        hotel=alpha,
        number="999",
        room_type="SUITE",
        capacity=4,
        price_per_night="50.00",
        is_active=False,
    )
    hidden_room = Room.objects.create(
        hotel=hidden, number="001", room_type="SINGLE", capacity=1, price_per_night="25.00"
    )
    return {
        "alpha": alpha,
        "beta": beta,
        "hidden": hidden,
        "single": single,
        "double": double,
        "suite": suite,
        "inactive": inactive,
        "hidden_room": hidden_room,
    }


def ids(response):
    return [obj.pk for obj in response.context["page_obj"]]


def add_booking(user, room, check_in, check_out, status=Booking.Status.PENDING):
    return Booking.objects.create(
        user=user,
        room=room,
        check_in=check_in,
        check_out=check_out,
        guests_count=1,
        price_per_night_snapshot=room.price_per_night,
        total_price=Decimal(str(room.price_per_night)) * (check_out - check_in).days,
        status=status,
    )


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_catalogue_requires_login(client, endpoint):
    path = reverse(f"hotels:{endpoint}")
    response = client.get(path)
    assert response.status_code == 302
    assert response.url == f"{reverse('accounts:login')}?next={path}"


@pytest.mark.parametrize("role", ["CUSTOMER", "MANAGER", "ADMIN"])
def test_all_roles_can_view_both_tables(client, customer, catalogue, role):
    customer.groups.set([Group.objects.get(name=role)])
    client.force_login(customer)
    for endpoint in ("hotel_list", "room_list"):
        response = client.get(reverse(f"hotels:{endpoint}"))
        assert response.status_code == 200
        assert b"<table>" in response.content


def test_superuser_can_view_catalogue_without_groups(client):
    user = User.objects.create_superuser(
        "catalogue_admin", "catalogue_admin@example.test", "Test-only-catalogue-729!"
    )
    client.force_login(user)
    for endpoint in ("hotel_list", "room_list"):
        assert client.get(reverse(f"hotels:{endpoint}")).status_code == 200


def test_catalogue_requires_each_permission_even_for_staff_and_ajax(client, customer):
    customer.groups.clear()
    customer.is_staff = True
    customer.save(update_fields=["is_staff"])
    client.force_login(customer)
    for endpoint in ("hotel_list", "room_list"):
        assert (
            client.get(
                reverse(f"hotels:{endpoint}"), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
            ).status_code
            == 403
        )
    customer.user_permissions.add(Permission.objects.get(codename="view_hotel"))
    assert client.get(reverse("hotels:hotel_list")).status_code == 200
    assert client.get(reverse("hotels:room_list")).status_code == 403


def test_tables_show_only_active_records(client, customer, catalogue):
    client.force_login(customer)
    hotels = client.get(reverse("hotels:hotel_list"))
    rooms = client.get(reverse("hotels:room_list"))
    assert ids(hotels) == [catalogue["alpha"].pk, catalogue["beta"].pk]
    assert ids(rooms) == [catalogue[name].pk for name in ("single", "double", "suite")]
    choices = rooms.context["form"].fields["hotel"].queryset
    assert list(choices) == [catalogue["alpha"], catalogue["beta"]]
    assert b"200.00 PLN" in rooms.content
    assert f"?hotel={catalogue['alpha'].pk}".encode() in hotels.content


@pytest.mark.parametrize(
    "filters,key",
    [
        ({"name": " ALpHa "}, "alpha"),
        ({"city": "wArSaW"}, "alpha"),
        ({"stars": "5"}, "beta"),
        ({"name": "hotel", "city": "Krakow", "stars": "5"}, "beta"),
    ],
)
def test_hotel_filters(client, customer, catalogue, filters, key):
    client.force_login(customer)
    response = client.get(reverse("hotels:hotel_list"), filters)
    assert ids(response) == [catalogue[key].pk]
    assert not response.context["form"].errors


@pytest.mark.parametrize(
    "filters",
    [
        {"stars": "6"},
        {"stars": "0"},
        {"stars": "bad"},
        {"name": "x" * 151},
        {"city": "x" * 101},
    ],
)
def test_invalid_hotel_filters_display_errors_without_results(client, customer, catalogue, filters):
    client.force_login(customer)
    response = client.get(reverse("hotels:hotel_list"), filters)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert ids(response) == []
    assert b"Correct the filters" in response.content


@pytest.mark.parametrize(
    "filters,keys",
    [
        ({"room_type": "DOUBLE"}, ["double"]),
        ({"capacity": "2"}, ["double", "suite"]),
        ({"min_price": "200.00"}, ["double", "suite"]),
        ({"max_price": "200.00"}, ["single", "double"]),
        ({"min_price": "200.00", "max_price": "200.00"}, ["double"]),
        ({"min_price": "0", "max_price": "0"}, []),
        (
            {"room_type": "SUITE", "capacity": "3", "min_price": "400", "max_price": "600"},
            ["suite"],
        ),
    ],
)
def test_room_filters(client, customer, catalogue, filters, keys):
    client.force_login(customer)
    response = client.get(reverse("hotels:room_list"), filters)
    assert not response.context["form"].errors
    assert ids(response) == [catalogue[key].pk for key in keys]


def test_room_hotel_filter_and_combination(client, customer, catalogue):
    client.force_login(customer)
    response = client.get(reverse("hotels:room_list"), {"hotel": catalogue["alpha"].pk})
    assert ids(response) == [catalogue["single"].pk, catalogue["double"].pk]
    response = client.get(
        reverse("hotels:room_list"), {"hotel": catalogue["alpha"].pk, "room_type": "SUITE"}
    )
    assert ids(response) == []


@pytest.mark.parametrize(
    "filters",
    [
        {"room_type": "PENTHOUSE"},
        {"capacity": "0"},
        {"capacity": "-1"},
        {"capacity": "1.5"},
        {"capacity": "32768"},
        {"capacity": "abc"},
        {"min_price": "-0.01"},
        {"max_price": "bad"},
        {"min_price": "NaN"},
        {"max_price": "Infinity"},
        {"max_price": "100000000.00"},
        {"min_price": "0.001"},
        {"min_price": "500", "max_price": "100"},
        {"hotel": "999999"},
        {"hotel": "bad"},
        {"check_in": "not-a-date", "check_out": "2030-01-03"},
        {"check_in": "2030-02-30", "check_out": "2030-03-02"},
    ],
)
def test_invalid_room_filters_do_not_crash_or_show_unfiltered_results(
    client, customer, catalogue, filters
):
    client.force_login(customer)
    response = client.get(reverse("hotels:room_list"), filters)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert ids(response) == []


def test_inactive_hotel_is_not_accepted_as_filter(client, customer, catalogue):
    client.force_login(customer)
    response = client.get(reverse("hotels:room_list"), {"hotel": catalogue["hidden"].pk})
    assert "hotel" in response.context["form"].errors
    assert ids(response) == []


@pytest.mark.parametrize("start,end", [(0, None), (None, 1), (-1, 1), (1, 1), (2, 1)])
def test_invalid_date_pairs(client, customer, catalogue, start, end):
    client.force_login(customer)
    today = timezone.localdate()
    parameters = {}
    if start is not None:
        parameters["check_in"] = today + timedelta(days=start)
    if end is not None:
        parameters["check_out"] = today + timedelta(days=end)
    response = client.get(reverse("hotels:room_list"), parameters)
    assert response.context["form"].errors
    assert ids(response) == []


def test_today_is_accepted_and_business_timezone_is_warsaw(client, customer, catalogue):
    assert settings.TIME_ZONE == "Europe/Warsaw"
    client.force_login(customer)
    today = timezone.localdate()
    response = client.get(
        reverse("hotels:room_list"), {"check_in": today, "check_out": today + timedelta(days=1)}
    )
    assert not response.context["form"].errors
    assert len(ids(response)) == 3


@pytest.mark.parametrize(
    "status,blocked", [("PENDING", True), ("CONFIRMED", True), ("CANCELLED", False)]
)
@pytest.mark.parametrize(
    "start,end,overlaps",
    [
        (10, 13, True),
        (9, 14, True),
        (11, 12, True),
        (8, 11, True),
        (12, 15, True),
        (8, 10, False),
        (13, 15, False),
        (1, 3, False),
        (20, 22, False),
    ],
)
def test_availability_overlap_status_and_adjacent_stays(
    client, customer, catalogue, status, blocked, start, end, overlaps
):
    client.force_login(customer)
    base = timezone.localdate() + timedelta(days=30)
    add_booking(
        customer, catalogue["double"], base + timedelta(days=10), base + timedelta(days=13), status
    )
    response = client.get(
        reverse("hotels:room_list"),
        {"check_in": base + timedelta(days=start), "check_out": base + timedelta(days=end)},
    )
    assert not response.context["form"].errors
    assert (catalogue["double"].pk not in ids(response)) == (blocked and overlaps)
    assert catalogue["single"].pk in ids(response) and catalogue["suite"].pk in ids(response)


def test_availability_does_not_combine_conditions_from_separate_bookings(
    client, customer, catalogue
):
    client.force_login(customer)
    base = timezone.localdate() + timedelta(days=30)
    add_booking(customer, catalogue["double"], base, base + timedelta(days=2))
    add_booking(customer, catalogue["double"], base + timedelta(days=5), base + timedelta(days=7))
    response = client.get(
        reverse("hotels:room_list"),
        {"check_in": base + timedelta(days=2), "check_out": base + timedelta(days=5)},
    )
    assert catalogue["double"].pk in ids(response)


def test_room_list_without_dates_includes_booked_rooms(client, customer, catalogue):
    client.force_login(customer)
    today = timezone.localdate()
    add_booking(customer, catalogue["double"], today, today + timedelta(days=2))
    assert catalogue["double"].pk in ids(client.get(reverse("hotels:room_list")))


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_pagination_is_stable_and_keeps_filters(client, customer, catalogue, endpoint):
    client.force_login(customer)
    for i in range(23):
        hotel = Hotel.objects.create(
            name=f"Page Hotel {i:02}", city="Filter City", address="Page Street", stars=4
        )
        Room.objects.create(
            hotel=hotel,
            number=f"{i:03}",
            room_type="DOUBLE",
            capacity=2,
            price_per_night=Decimal("123.45"),
        )
    filters = (
        {"city": "Filter City", "stars": "4"}
        if endpoint == "hotel_list"
        else {"room_type": "DOUBLE", "min_price": "123.45", "max_price": "123.45"}
    )
    path = reverse(f"hotels:{endpoint}")
    first = client.get(path, filters)
    second = client.get(path, {**filters, "page": "2"})
    last = client.get(path, {**filters, "page": "3"})
    assert [len(ids(response)) for response in (first, second, last)] == [10, 10, 3]
    all_ids = ids(first) + ids(second) + ids(last)
    assert len(set(all_ids)) == 23
    assert ids(client.get(path, filters)) == ids(first)
    for key, value in filters.items():
        assert parse_qs(second.context["filter_query"])[key] == [value]
    assert "page" not in parse_qs(second.context["filter_query"])
    assert b"Previous" in second.content and b"Next" in second.content
    assert b"Page 3 of 3" in last.content


@pytest.mark.parametrize("page,expected", [("nonsense", 1), ("999999", 2), ("0", 2), ("-1", 2)])
def test_invalid_page_numbers_have_safe_fallbacks(client, customer, page, expected):
    client.force_login(customer)
    for i in range(11):
        Hotel.objects.create(name=f"Hotel {i:02}", city="Warsaw", address="Street", stars=3)
    response = client.get(reverse("hotels:hotel_list"), {"page": page})
    assert response.status_code == 200
    assert response.context["page_obj"].number == expected


@pytest.mark.parametrize("endpoint", ["hotel_list", "room_list"])
def test_empty_catalogue_renders_table_and_empty_state(client, customer, endpoint):
    client.force_login(customer)
    response = client.get(reverse(f"hotels:{endpoint}"))
    assert response.status_code == 200 and ids(response) == []
    assert b"No hotels match" in response.content or b"No rooms match" in response.content
    assert b"<table>" in response.content and b"Results pages" not in response.content


def test_rendering_escapes_record_and_filter_text(client, customer, catalogue):
    client.force_login(customer)
    Hotel.objects.filter(pk=catalogue["alpha"].pk).update(name="<script>alert(1)</script>")
    response = client.get(reverse("hotels:hotel_list"))
    assert b"&lt;script&gt;" in response.content and b"<script>" not in response.content
    response = client.get(reverse("hotels:hotel_list"), {"name": '"><script>alert(1)</script>'})
    assert b"<script>" not in response.content


def test_room_rendering_does_not_add_per_row_hotel_queries(client, customer, catalogue):
    client.force_login(customer)
    path = reverse("hotels:room_list")
    with CaptureQueriesContext(connection) as small:
        client.get(path)
    for i in range(8):
        Room.objects.create(
            hotel=catalogue["alpha"],
            number=f"extra{i}",
            room_type="DOUBLE",
            capacity=2,
            price_per_night="150.00",
        )
    with CaptureQueriesContext(connection) as large:
        response = client.get(path)
    assert len(ids(response)) == 10
    assert len(large) == len(small)


def test_catalogue_links_are_visible_to_customer(client, customer):
    client.force_login(customer)
    for endpoint in ("accounts:home", "accounts:customer_area"):
        html = client.get(reverse(endpoint)).content.decode()
        assert f'href="{reverse("hotels:hotel_list")}"' in html
        assert f'href="{reverse("hotels:room_list")}"' in html


def test_filter_forms_clean_typed_values(catalogue):
    hotels = HotelFilterForm({"name": " Alpha ", "stars": "3"})
    assert hotels.is_valid()
    assert hotels.cleaned_data["name"] == "Alpha" and hotels.cleaned_data["stars"] == 3
    rooms = RoomFilterForm({"hotel": catalogue["alpha"].pk, "capacity": "2", "min_price": "12.34"})
    assert rooms.is_valid()
    assert rooms.cleaned_data["hotel"] == catalogue["alpha"]
    assert rooms.cleaned_data["capacity"] == 2 and rooms.cleaned_data["min_price"] == Decimal(
        "12.34"
    )


def test_queries_are_read_only_and_post_is_not_supported(client, customer, catalogue):
    client.force_login(customer)
    models = [User, Hotel, Room, Booking]
    before = {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}
    for endpoint in ("hotel_list", "room_list"):
        path = reverse(f"hotels:{endpoint}")
        client.get(path)
        client.get(path, {"page": 2})
        assert client.post(path, {"name": "Do not create"}).status_code == 405
    after = {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}
    assert before == after
