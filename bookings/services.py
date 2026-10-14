from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from hotels.models import Hotel, Room

from .models import Booking
from .validation import validate_stay


def locked_room(room_id):
    try:
        room = Room.objects.select_for_update().get(pk=room_id)
    except Room.DoesNotExist as exc:
        raise ValidationError("This room is no longer available.") from exc
    room.hotel = Hotel.objects.select_for_update().get(pk=room.hotel_id)
    return room


@transaction.atomic
def create_booking(*, actor, room_id, check_in, check_out, guests_count):
    if not actor.has_perm("bookings.add_booking"):
        raise PermissionDenied
    room = locked_room(room_id)
    if not room.is_active or not room.hotel.is_active:
        raise ValidationError("This room or hotel is no longer active.")
    validate_stay(room=room, check_in=check_in, check_out=check_out, guests_count=guests_count)
    if Booking.objects.filter(
        room=room,
        status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
        check_in__lt=check_out,
        check_out__gt=check_in,
    ).exists():
        raise ValidationError(
            "This room is already booked for the selected dates. Choose another stay."
        )
    booking = Booking(
        user=actor,
        room=room,
        check_in=check_in,
        check_out=check_out,
        guests_count=guests_count,
        price_per_night_snapshot=room.price_per_night,
        total_price=room.price_per_night * (check_out - check_in).days,
        status=Booking.Status.PENDING,
    )
    booking.full_clean()
    booking.save()
    return booking


@transaction.atomic
def change_booking_status(*, actor, booking_id, status):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied
    if status not in (Booking.Status.CONFIRMED, Booking.Status.CANCELLED):
        raise ValidationError("Choose Confirmed or Cancelled as the new status.")
    try:
        room_id = Booking.objects.values_list("room_id", flat=True).get(pk=booking_id)
        room = locked_room(room_id)
        booking = Booking.objects.select_for_update().get(pk=booking_id, room_id=room.pk)
    except Booking.DoesNotExist as exc:
        raise ValidationError("Booking not found.") from exc
    manager = actor.has_perm("bookings.change_booking")
    if status == Booking.Status.CONFIRMED:
        if not manager:
            raise PermissionDenied
        if booking.status != Booking.Status.PENDING:
            raise ValidationError("Only pending bookings can be confirmed.")
        if booking.check_in < timezone.localdate():
            raise ValidationError("A past stay cannot be confirmed.")
    else:
        if not manager and booking.user_id != actor.pk:
            raise PermissionDenied
        if booking.status == Booking.Status.CANCELLED:
            return booking
        if booking.check_in <= timezone.localdate() and (
            not manager or booking.status == Booking.Status.CONFIRMED
        ):
            raise ValidationError("This stay has already started and cannot be cancelled.")
    booking.status = status
    booking.full_clean()
    booking.save(update_fields=["status", "updated_at"])
    return booking
