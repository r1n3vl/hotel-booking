from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.access import permission_required
from bookings.models import Booking

from .forms import HotelFilterForm, RoomFilterForm
from .models import Hotel, Room

PAGE_SIZE = 10


def catalogue_context(request, form, records):
    parameters = request.GET.copy()
    parameters.pop("page", None)
    return {
        "form": form,
        "page_obj": Paginator(records, PAGE_SIZE).get_page(request.GET.get("page")),
        "filter_query": parameters.urlencode(),
    }


@permission_required("hotels.view_hotel")
@require_GET
def hotel_list(request):
    form = HotelFilterForm(request.GET)
    hotels = Hotel.objects.filter(is_active=True).order_by("name", "city", "pk")
    if form.is_valid():
        data = form.cleaned_data
        if data["name"]:
            hotels = hotels.filter(name__icontains=data["name"])
        if data["city"]:
            hotels = hotels.filter(city__icontains=data["city"])
        if data["stars"] is not None:
            hotels = hotels.filter(stars=data["stars"])
    else:
        hotels = hotels.none()
    return render(request, "hotels/hotel_list.html", catalogue_context(request, form, hotels))


@permission_required("hotels.view_room")
@require_GET
def room_list(request):
    form = RoomFilterForm(request.GET)
    rooms = (
        Room.objects.filter(is_active=True, hotel__is_active=True)
        .select_related("hotel")
        .order_by("hotel__name", "number", "pk")
    )
    if form.is_valid():
        data = form.cleaned_data
        if data["hotel"]:
            rooms = rooms.filter(hotel=data["hotel"])
        if data["room_type"]:
            rooms = rooms.filter(room_type=data["room_type"])
        if data["capacity"] is not None:
            rooms = rooms.filter(capacity__gte=data["capacity"])
        if data["min_price"] is not None:
            rooms = rooms.filter(price_per_night__gte=data["min_price"])
        if data["max_price"] is not None:
            rooms = rooms.filter(price_per_night__lte=data["max_price"])
        if data["check_in"] and data["check_out"]:
            occupied = Booking.objects.filter(
                status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
                check_in__lt=data["check_out"],
                check_out__gt=data["check_in"],
            ).values("room_id")
            rooms = rooms.exclude(pk__in=occupied)
    else:
        rooms = rooms.none()
    return render(request, "hotels/room_list.html", catalogue_context(request, form, rooms))
