from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from accounts.roles import ADMIN, ROLE_NAMES


class Command(BaseCommand):
    help = "Assign one application role; ADMIN also receives Django admin staff access."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=ROLE_NAMES)

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = User.objects.select_for_update().get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError("User not found.") from exc
        if user.is_superuser:
            raise CommandError(
                "Superusers already have all access; this command does not change them."
            )
        try:
            role = Group.objects.get(name=options["role"])
        except Group.DoesNotExist as exc:
            raise CommandError("Run migrate and setup_roles first.") from exc
        user.groups.remove(*user.groups.filter(name__in=ROLE_NAMES))
        user.groups.add(role)
        user.is_staff = options["role"] == ADMIN
        user.save(update_fields=["is_staff"])
        self.stdout.write(self.style.SUCCESS("Application role assigned."))
        if user.user_permissions.exists() or user.groups.exclude(name__in=ROLE_NAMES).exists():
            self.stdout.write(
                "Additional direct permissions or unrelated groups were preserved; review them separately."
            )
