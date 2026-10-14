from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Hotel(models.Model):
    name = models.CharField(max_length=150)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    stars = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5),
                name="hotel_stars_between_1_and_5",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.city})"

class Room(models.Model):
    class RoomType(models.TextChoices):
        SINGLE = "SINGLE", "Single"
        DOUBLE = "DOUBLE", "Double"
        SUITE = "SUITE", "Suite"

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="rooms",
    )

    number = models.CharField(max_length=20)

    room_type = models.CharField(
        max_length=10,
        choices=RoomType.choices,
    )

    capacity = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)]
    )

    price_per_night = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "number"],
                name="unique_room_number_per_hotel",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="room_capacity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_night__gt=0),
                name="room_price_positive",
            ),
        ]

    def __str__(self):
        return f"{self.hotel.name} — room {self.number}"
