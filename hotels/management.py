from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from accounts.access import permission_required
from bookings.models import Booking
from bookings.services import locked_room

from .management_forms import (
    HotelForm,
    HotelManagementFilterForm,
    RoomForm,
    RoomManagementFilterForm,
)
from .models import Hotel, Room
from .views import catalogue_context


@permission_required("hotels.change_hotel")
@require_GET
def hotel_list(request):
    form = HotelManagementFilterForm(request.GET)
    hotels = Hotel.objects.order_by("name", "city", "pk")
    if form.is_valid():
        data = form.cleaned_data
        for field in ("name", "city"):
            if data[field]:
                hotels = hotels.filter(**{f"{field}__icontains": data[field]})
        if data["stars"] is not None:
            hotels = hotels.filter(stars=data["stars"])
        if data["active"]:
            hotels = hotels.filter(is_active=data["active"] == "active")
    else:
        hotels = hotels.none()
    return render(request, "hotels/manage_hotels.html", catalogue_context(request, form, hotels))


@permission_required("hotels.change_room")
@require_GET
def room_list(request):
    form = RoomManagementFilterForm(request.GET)
    rooms = Room.objects.select_related("hotel").order_by("hotel__name", "number", "pk")
    if form.is_valid():
        data = form.cleaned_data
        if data["hotel"]:
            rooms = rooms.filter(hotel=data["hotel"])
        if data["number"]:
            rooms = rooms.filter(number__icontains=data["number"])
        if data["room_type"]:
            rooms = rooms.filter(room_type=data["room_type"])
        if data["active"]:
            rooms = rooms.filter(is_active=data["active"] == "active")
    else:
        rooms = rooms.none()
    return render(request, "hotels/manage_rooms.html", catalogue_context(request, form, rooms))


@permission_required("hotels.add_hotel")
@require_http_methods(["GET", "POST"])
def hotel_create(request):
    return hotel_editor(request)


@permission_required("hotels.change_hotel")
@require_http_methods(["GET", "POST"])
def hotel_update(request, pk):
    return hotel_editor(request, pk=pk)


def hotel_editor(request, pk=None):
    hotel = get_object_or_404(Hotel, pk=pk) if pk is not None else None
    form = HotelForm(request.POST if request.method == "POST" else None, instance=hotel)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            hotel = (
                get_object_or_404(Hotel.objects.select_for_update(), pk=pk)
                if pk is not None
                else None
            )
            form = HotelForm(request.POST, instance=hotel)
            if form.is_valid():
                form.save()
                messages.success(request, "Hotel saved.")
                return redirect("hotels:manage_hotels")
    return render(
        request,
        "hotels/editor.html",
        {
            "form": form,
            "title": "Edit hotel" if pk is not None else "Add hotel",
            "back_url": "hotels:manage_hotels",
        },
    )


@permission_required("hotels.add_room")
@require_http_methods(["GET", "POST"])
def room_create(request):
    return room_editor(request)


@permission_required("hotels.change_room")
@require_http_methods(["GET", "POST"])
def room_update(request, pk):
    return room_editor(request, pk=pk)


def room_editor(request, pk=None):
    room = get_object_or_404(Room, pk=pk) if pk is not None else None
    form = RoomForm(request.POST if request.method == "POST" else None, instance=room)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                if pk is not None:
                    room = locked_room(pk)
                else:
                    get_object_or_404(
                        Hotel.objects.select_for_update(), pk=form.cleaned_data["hotel"].pk
                    )
                form = RoomForm(request.POST, instance=room)
                if form.is_valid():
                    if (
                        pk is not None
                        and Booking.objects.filter(
                            room=room,
                            status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
                            check_out__gt=timezone.localdate(),
                            guests_count__gt=form.cleaned_data["capacity"],
                        ).exists()
                    ):
                        form.add_error(
                            "capacity",
                            "Capacity cannot be lower than the guest count of an ongoing or future booking.",
                        )
                    else:
                        form.save()
                        messages.success(request, "Room saved.")
                        return redirect("hotels:manage_rooms")
        except (IntegrityError, ValidationError) as exc:
            text = (
                "A room with this hotel and number already exists."
                if isinstance(exc, IntegrityError)
                else " ".join(exc.messages)
            )
            form.add_error(None, text)
    return render(
        request,
        "hotels/editor.html",
        {
            "form": form,
            "title": "Edit room" if pk is not None else "Add room",
            "back_url": "hotels:manage_rooms",
        },
    )
