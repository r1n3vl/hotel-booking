from django.core.management.base import BaseCommand

from accounts.roles import provision_roles


class Command(BaseCommand):
    help = "Idempotently restore CUSTOMER, MANAGER and ADMIN permission policies."

    def handle(self, *args, **options):
        provision_roles()
        self.stdout.write(self.style.SUCCESS("CUSTOMER, MANAGER and ADMIN roles provisioned."))
