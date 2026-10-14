import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from accounts.roles import ADMIN, CUSTOMER, MANAGER
from bookings.models import Booking
from bookings.services import create_booking
from hotels.models import Hotel, Room


class Command(BaseCommand):
    help = "Explicitly create demo accounts and inventory without resetting existing records."

    @transaction.atomic
    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD", "")
        if not password:
            raise CommandError(
                "Set DEMO_PASSWORD in the environment for this explicit demo command."
            )
        try:
            validate_password(password)
            groups = {role: Group.objects.get(name=role) for role in (CUSTOMER, MANAGER, ADMIN)}
            users = {}
            for role in (CUSTOMER, MANAGER, ADMIN):
                username = f"sample_{role.lower()}"
                email = f"{username}@example.test"
                user, created = User.objects.get_or_create(
                    username=username, defaults={"email": email}
                )
                if user.email != email:
                    raise CommandError(
                        "A demo username belongs to another account; no changes applied."
                    )
                if created:
                    validate_password(password, user)
                    user.set_password(password)
                    user.is_staff = role == ADMIN
                    user.full_clean()
                    user.save()
                    user.groups.add(groups[role])
                users[role] = user
            hotel, _ = Hotel.objects.get_or_create(
                name="Learning Demo Hotel",
                city="Warsaw",
                address="Demo Street 9",
                defaults={
                    "stars": 4,
                    "description": "Explicitly generated demonstration inventory.",
                },
            )
            rooms = []
            for number, room_type, capacity, price in [
                ("D101", "SINGLE", 1, "120.00"),
                ("D201", "DOUBLE", 2, "240.00"),
                ("D401", "SUITE", 4, "480.00"),
            ]:
                room, _ = Room.objects.get_or_create(
                    hotel=hotel,
                    number=number,
                    defaults={
                        "room_type": room_type,
                        "capacity": capacity,
                        "price_per_night": Decimal(price),
                    },
                )
                rooms.append(room)
            if not Booking.objects.filter(user=users[CUSTOMER], room=rooms[0]).exists():
                start = timezone.localdate() + timedelta(days=7)
                create_booking(
                    actor=users[CUSTOMER],
                    room_id=rooms[0].pk,
                    check_in=start,
                    check_out=start + timedelta(days=2),
                    guests_count=1,
                )
        except (ValidationError, IntegrityError, Group.DoesNotExist) as exc:
            raise CommandError(
                "Demo data could not be created safely. Check password strength, existing records and migrated roles; no changes applied."
            ) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Demo records ready. Existing passwords, roles and records were preserved."
            )
        )
