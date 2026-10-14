from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CANCELLED = "CANCELLED", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
    )

    room = models.ForeignKey(
        "hotels.Room",
        on_delete=models.PROTECT,
        related_name="bookings",
    )

    check_in = models.DateField()
    check_out = models.DateField()

    guests_count = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)]
    )

    price_per_night_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(check_out__gt=models.F("check_in")),
                name="booking_checkout_after_checkin",
            ),
            models.CheckConstraint(
                condition=models.Q(guests_count__gte=1),
                name="booking_guests_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_night_snapshot__gt=0),
                name="booking_nightly_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(total_price__gt=0),
                name="booking_total_price_positive",
            ),
        ]

    def __str__(self):
        return f"Booking {self.pk} — {self.check_in} to {self.check_out}"

