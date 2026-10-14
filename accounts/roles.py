from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q

CUSTOMER = "CUSTOMER"
MANAGER = "MANAGER"
ADMIN = "ADMIN"
ROLE_NAMES = (CUSTOMER, MANAGER, ADMIN)

CUSTOMER_PERMISSIONS = {
    "accounts.access_customer_area",
    "hotels.view_hotel",
    "hotels.view_room",
    "bookings.add_booking",
    "bookings.view_booking",
}
MANAGER_PERMISSIONS = CUSTOMER_PERMISSIONS | {
    "accounts.access_manager_area",
    "hotels.add_hotel",
    "hotels.change_hotel",
    "hotels.add_room",
    "hotels.change_room",
    "bookings.change_booking",
}


@transaction.atomic
def provision_roles(using="default"):
    permissions = Permission.objects.using(using).select_related("content_type")
    policies = {CUSTOMER: CUSTOMER_PERMISSIONS, MANAGER: MANAGER_PERMISSIONS}
    for role, names in policies.items():
        group, _ = Group.objects.using(using).get_or_create(name=role)
        group.permissions.set(
            [p for p in permissions if f"{p.content_type.app_label}.{p.codename}" in names]
        )
    group, _ = Group.objects.using(using).get_or_create(name=ADMIN)
    group.permissions.set(
        permissions.filter(
            Q(content_type__app_label__in=["accounts", "hotels", "bookings", "auth"])
        )
    )


def provision_roles_after_migrate(sender, using, **kwargs):
    provision_roles(using=using)
