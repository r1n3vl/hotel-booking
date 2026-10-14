from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_stay(*, room, check_in, check_out, guests_count):
    errors = {}
    if check_in < timezone.localdate():
        errors["check_in"] = "Check-in must be today or later."
    if check_out <= check_in:
        errors["check_out"] = "Check-out must be after check-in."
    if not 1 <= guests_count <= room.capacity:
        errors["guests_count"] = f"Choose between 1 and {room.capacity} guests for this room."
    if errors:
        raise ValidationError(errors)
