from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

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


def catalogue_response(request, name, form, records):
    context = catalogue_context(request, form, records)
    context["ajax_catalogue"] = True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        page = context["page_obj"]
        return JsonResponse(
            {
                "html": render_to_string(f"hotels/{name}_fragment.html", context, request=request),
                "count": page.paginator.count,
                "page": page.number,
                "valid": not bool(form.errors),
            }
        )
    return render(request, f"hotels/{name}_list.html", context)


@vary_on_headers("X-Requested-With")
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
    return catalogue_response(request, "hotel", form, hotels)


@vary_on_headers("X-Requested-With")
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
    return catalogue_response(request, "room", form, rooms)
