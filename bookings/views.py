from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from accounts.access import permission_required
from hotels.models import Room

from .forms import BookingForm
from .models import Booking
from .services import create_booking


@permission_required("bookings.add_booking")
@require_http_methods(["GET", "POST"])
def booking_create(request, room_id):
    room = get_object_or_404(
        Room.objects.select_related("hotel"), pk=room_id, is_active=True, hotel__is_active=True
    )
    initial = {name: request.GET.get(name, "") for name in ("check_in", "check_out")}
    initial["guests_count"] = 1
    form = BookingForm(
        request.POST if request.method == "POST" else None, room=room, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        try:
            booking = create_booking(actor=request.user, room_id=room.pk, **form.cleaned_data)
        except ValidationError as exc:
            room = get_object_or_404(Room.objects.select_related("hotel"), pk=room.pk)
            form = BookingForm(request.POST, room=room)
            form.add_error(None, " ".join(exc.messages))
        else:
            return redirect("bookings:detail", pk=booking.pk)
    return render(request, "bookings/create.html", {"form": form, "room": room})


@permission_required("bookings.view_booking")
@require_GET
def booking_detail(request, pk):
    bookings = Booking.objects.select_related("room__hotel", "user")
    if not request.user.has_perm("bookings.change_booking"):
        bookings = bookings.filter(user=request.user)
    booking = get_object_or_404(bookings, pk=pk)
    manager = request.user.has_perm("bookings.change_booking")
    today = timezone.localdate()
    return render(
        request,
        "bookings/detail.html",
        {
            "booking": booking,
            "nights": (booking.check_out - booking.check_in).days,
            "can_confirm": manager
            and booking.status == Booking.Status.PENDING
            and booking.check_in >= today,
            "can_cancel": booking.status in (Booking.Status.PENDING, Booking.Status.CONFIRMED)
            and (
                booking.check_in > today or (manager and booking.status == Booking.Status.PENDING)
            ),
        },
    )
