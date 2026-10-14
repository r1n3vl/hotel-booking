from importlib import import_module
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from accounts.forms import AdminUserChangeForm, AdminUserCreationForm, RegistrationForm
from accounts.models import UserManager
from accounts.roles import CUSTOMER_PERMISSIONS, MANAGER_PERMISSIONS, provision_roles

User = get_user_model()
pytestmark = pytest.mark.django_db
TEST_PASSWORD = "Test-only-long-passphrase-719!"


@pytest.fixture
def registration_data():
    return {
        "username": "new_customer",
        "email": "  Customer@EXAMPLE.TEST  ",
        "password1": TEST_PASSWORD,
        "password2": TEST_PASSWORD,
    }


@pytest.fixture
def customer():
    user = User.objects.create_user("customer", "customer@example.test", TEST_PASSWORD)
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


@pytest.fixture
def superuser():
    return User.objects.create_superuser("superuser", "superuser@example.test", TEST_PASSWORD)


def permission_names(group):
    return {
        f"{p.content_type.app_label}.{p.codename}"
        for p in group.permissions.select_related("content_type")
    }


def test_tests_use_postgresql():
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_")


def test_custom_user_is_registered_with_correct_admin():
    registered = admin.site._registry[User]
    assert registered.add_form is AdminUserCreationForm
    assert registered.form is AdminUserChangeForm
    assert "email" in registered.add_fieldsets[0][1]["fields"]


def test_registration_creates_only_customer_and_hashes_password(client, registration_data):
    registration_data.update(
        {
            "is_staff": "on",
            "is_superuser": "on",
            "groups": [Group.objects.get(name="ADMIN").pk],
            "user_permissions": list(Permission.objects.values_list("pk", flat=True)),
            "role": "ADMIN",
        }
    )
    response = client.post(reverse("accounts:register"), registration_data)
    assert response.status_code == 302
    assert response.url == reverse("accounts:home")
    user = User.objects.get(username="new_customer")
    assert user.email == "customer@example.test"
    assert user.check_password(TEST_PASSWORD)
    assert user.password != TEST_PASSWORD
    assert not user.is_staff and not user.is_superuser
    assert list(user.groups.values_list("name", flat=True)) == ["CUSTOMER"]
    assert not user.user_permissions.exists()
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.get(reverse("accounts:customer_area")).status_code == 200
    assert client.get(reverse("accounts:manager_area")).status_code == 403
    assert client.get(reverse("accounts:admin_area")).status_code == 403


@pytest.mark.parametrize(
    "field,value",
    [
        ("username", ""),
        ("username", "invalid username!"),
        ("username", "a" * 151),
        ("email", ""),
        ("email", "not-an-email"),
        ("email", "a" * 245 + "@example.test"),
        ("password1", "123"),
        ("password2", "different-passphrase"),
        ("password1", "1234567890"),
    ],
)
def test_invalid_registration_does_not_create_user(client, registration_data, field, value):
    registration_data[field] = value
    before = User.objects.count()
    response = client.post(reverse("accounts:register"), registration_data)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert User.objects.count() == before
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize(
    "field,value",
    [
        ("username", "customer"),
        ("email", " CUSTOMER@EXAMPLE.TEST "),
    ],
)
def test_registration_rejects_duplicates(client, customer, registration_data, field, value):
    registration_data[field] = value
    response = client.post(reverse("accounts:register"), registration_data)
    assert field in response.context["form"].errors
    assert User.objects.count() == 1


def test_registration_handles_database_uniqueness_race(client, registration_data):
    with patch("accounts.views.RegistrationForm.save", side_effect=IntegrityError("conflict")):
        response = client.post(reverse("accounts:register"), registration_data)
    assert response.status_code == 200
    assert "already registered" in response.context["form"].non_field_errors()[0]
    assert not User.objects.exists()


def test_authenticated_user_cannot_register_another_account(client, customer, registration_data):
    client.force_login(customer)
    for method in (client.get, client.post):
        response = method(reverse("accounts:register"), registration_data)
        assert response.status_code == 302
    assert User.objects.count() == 1


def test_registration_form_commit_false_does_not_save_or_grant_roles(registration_data):
    form = RegistrationForm(registration_data)
    assert form.is_valid(), form.errors
    user = form.save(commit=False)
    assert user.pk is None
    assert not user.is_staff and not user.is_superuser
    assert user.check_password(TEST_PASSWORD)
    assert not User.objects.exists()


def test_registration_only_allows_get_and_post(client):
    assert client.put(reverse("accounts:register")).status_code == 405


def test_login_success_and_logout(client, customer):
    response = client.post(
        reverse("accounts:login"), {"username": customer.username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:home")
    assert client.get(reverse("accounts:home")).status_code == 200
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.session["_auth_user_id"] == str(customer.pk)
    response = client.post(reverse("accounts:logout"))
    assert response.url == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
    assert client.get(reverse("accounts:home")).status_code == 302


@pytest.mark.parametrize(
    "username,password", [("missing", TEST_PASSWORD), ("customer", "wrong"), ("", "")]
)
def test_login_failure(client, customer, username, password):
    response = client.post(reverse("accounts:login"), {"username": username, "password": password})
    assert response.status_code == 200
    assert response.context["form"].non_field_errors() or response.context["form"].errors
    assert "_auth_user_id" not in client.session


def test_inactive_user_cannot_log_in(client, customer):
    customer.is_active = False
    customer.save(update_fields=["is_active"])
    response = client.post(
        reverse("accounts:login"), {"username": customer.username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize(
    "next_url,expected",
    [
        ("/customer/", "/customer/"),
        ("https://attacker.example/", "/"),
        ("//attacker.example/", "/"),
    ],
)
def test_login_redirect_validation(client, customer, next_url, expected):
    response = client.post(
        reverse("accounts:login"),
        {"username": customer.username, "password": TEST_PASSWORD, "next": next_url},
    )
    assert response.url == expected


@pytest.mark.parametrize("view", ["home", "customer_area", "manager_area", "admin_area"])
def test_anonymous_protected_pages_redirect_to_login(client, view):
    path = reverse(f"accounts:{view}")
    response = client.get(path)
    assert response.status_code == 302
    assert response.url == f"{reverse('accounts:login')}?next={path}"


@pytest.mark.parametrize(
    "role,expected",
    [
        ("CUSTOMER", [200, 403, 403]),
        ("MANAGER", [200, 200, 403]),
        ("ADMIN", [200, 200, 200]),
    ],
)
def test_role_access_matrix(client, customer, role, expected):
    customer.groups.set([Group.objects.get(name=role)])
    client.force_login(customer)
    urls = [reverse(f"accounts:{area}_area") for area in ("customer", "manager", "admin")]
    assert [client.get(url).status_code for url in urls] == expected
    assert [
        client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code for url in urls
    ] == expected


def test_ungrouped_staff_does_not_bypass_role_policy(client, customer):
    customer.groups.clear()
    customer.is_staff = True
    customer.save(update_fields=["is_staff"])
    client.force_login(customer)
    assert client.get(reverse("accounts:home")).status_code == 200
    for area in ("customer", "manager", "admin"):
        assert client.get(reverse(f"accounts:{area}_area")).status_code == 403


def test_explicit_django_permission_is_supported(client, customer):
    customer.groups.clear()
    customer.user_permissions.add(Permission.objects.get(codename="access_manager_area"))
    client.force_login(customer)
    assert client.get(reverse("accounts:manager_area")).status_code == 200
    assert client.get(reverse("accounts:admin_area")).status_code == 403


def test_superuser_can_access_every_area_and_admin_without_group(client, superuser):
    assert not superuser.groups.exists()
    client.force_login(superuser)
    for area in ("customer", "manager", "admin"):
        assert client.get(reverse(f"accounts:{area}_area")).status_code == 200
    assert client.get(reverse("admin:index")).status_code == 200
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 200
    assert client.get(reverse("admin:auth_group_changelist")).status_code == 200


def test_customer_and_manager_cannot_access_django_admin(client, customer):
    for role in ("CUSTOMER", "MANAGER"):
        call_command("set_role", customer.username, role, stdout=StringIO())
        customer.refresh_from_db()
        client.force_login(customer)
        response = client.get(reverse("admin:accounts_user_changelist"))
        assert response.status_code == 302
        assert reverse("admin:login") in response.url


def test_admin_role_can_manage_users_and_groups_without_being_superuser(client, customer):
    call_command("set_role", customer.username, "ADMIN", stdout=StringIO())
    customer.refresh_from_db()
    assert customer.is_staff and not customer.is_superuser
    client.force_login(customer)
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 200
    assert client.get(reverse("admin:auth_group_changelist")).status_code == 200


def test_admin_login_and_user_creation_require_email(client, superuser):
    response = client.post(
        reverse("admin:login"),
        {"username": superuser.username, "password": TEST_PASSWORD, "next": reverse("admin:index")},
    )
    assert response.status_code == 302
    response = client.post(
        reverse("admin:accounts_user_add"),
        {
            "username": "admin_created",
            "email": "  NEW@EXAMPLE.TEST  ",
            "password1": TEST_PASSWORD,
            "password2": TEST_PASSWORD,
            "usable_password": "true",
        },
    )
    assert response.status_code == 302
    user = User.objects.get(username="admin_created")
    assert user.email == "new@example.test" and user.check_password(TEST_PASSWORD)
    form = AdminUserCreationForm(
        {"username": "no_email", "password1": TEST_PASSWORD, "password2": TEST_PASSWORD}
    )
    assert not form.is_valid() and "email" in form.errors


def test_admin_forms_reject_duplicate_normalised_email(customer):
    form = AdminUserCreationForm(
        {
            "username": "someone_else",
            "email": " CUSTOMER@EXAMPLE.TEST ",
            "password1": TEST_PASSWORD,
            "password2": TEST_PASSWORD,
        }
    )
    assert not form.is_valid()
    assert "email" in form.errors
    other = User.objects.create_user("other", "other@example.test", TEST_PASSWORD)
    form = AdminUserChangeForm(
        {"username": "other", "email": " CUSTOMER@EXAMPLE.TEST ", "date_joined": other.date_joined},
        instance=other,
    )
    assert not form.is_valid()
    assert "email" in form.errors


def test_role_provisioning_restores_exact_policies_and_is_idempotent(customer):
    unrelated = Group.objects.create(name="UNRELATED")
    customer.groups.add(unrelated)
    manager = Group.objects.get(name="MANAGER")
    manager.permissions.add(Permission.objects.get(codename="change_user"))
    group_ids = list(Group.objects.order_by("pk").values_list("pk", flat=True))
    provision_roles()
    call_command("setup_roles", stdout=StringIO())
    assert list(Group.objects.order_by("pk").values_list("pk", flat=True)) == group_ids
    assert permission_names(Group.objects.get(name="CUSTOMER")) == CUSTOMER_PERMISSIONS
    assert permission_names(manager) == MANAGER_PERMISSIONS
    admin_permissions = permission_names(Group.objects.get(name="ADMIN"))
    assert MANAGER_PERMISSIONS < admin_permissions
    assert {
        "accounts.change_user",
        "auth.change_group",
        "accounts.access_admin_area",
    } <= admin_permissions
    assert set(customer.groups.values_list("name", flat=True)) == {"CUSTOMER", "UNRELATED"}


def test_post_migrate_recreates_roles():
    Group.objects.filter(name__in=["CUSTOMER", "MANAGER", "ADMIN"]).delete()
    call_command("migrate", verbosity=0, stdout=StringIO())
    assert Group.objects.filter(name__in=["CUSTOMER", "MANAGER", "ADMIN"]).count() == 3
    assert permission_names(Group.objects.get(name="MANAGER")) == MANAGER_PERMISSIONS


def test_set_role_replaces_only_app_roles_and_staff_flag(customer):
    unrelated = Group.objects.create(name="EXTERNAL")
    customer.groups.add(unrelated)
    direct = Permission.objects.get(codename="view_group")
    customer.user_permissions.add(direct)
    output = StringIO()
    call_command("set_role", customer.username, "ADMIN", stdout=output)
    customer.refresh_from_db()
    assert customer.is_staff and not customer.is_superuser
    assert set(customer.groups.values_list("name", flat=True)) == {"ADMIN", "EXTERNAL"}
    assert customer.user_permissions.filter(pk=direct.pk).exists()
    assert "preserved" in output.getvalue()
    call_command("set_role", customer.username, "MANAGER", stdout=StringIO())
    call_command("set_role", customer.username, "MANAGER", stdout=StringIO())
    customer.refresh_from_db()
    assert not customer.is_staff
    assert set(customer.groups.values_list("name", flat=True)) == {"MANAGER", "EXTERNAL"}


def test_set_role_errors_do_not_change_accounts(customer, superuser):
    with pytest.raises(CommandError, match="User not found"):
        call_command("set_role", "missing", "MANAGER")
    with pytest.raises(CommandError, match="Superusers"):
        call_command("set_role", superuser.username, "CUSTOMER")
    Group.objects.get(name="ADMIN").delete()
    with pytest.raises(CommandError, match="setup_roles"):
        call_command("set_role", customer.username, "ADMIN")
    customer.refresh_from_db()
    assert not customer.is_staff
    assert list(customer.groups.values_list("name", flat=True)) == ["CUSTOMER"]


@pytest.mark.parametrize(
    "email,expected", [("  User@EXAMPLE.TEST ", "user@example.test"), ("", ""), (None, "")]
)
def test_email_normalisation_function(email, expected):
    assert UserManager.normalize_email(email) == expected


def test_manager_save_and_clean_normalise_email():
    user = User.objects.create_user("normalised", "  MIXED@Example.Test ", TEST_PASSWORD)
    assert user.email == "mixed@example.test"
    user.email = "  CHANGED@EXAMPLE.TEST "
    user.save()
    user.refresh_from_db()
    assert user.email == "changed@example.test"
    user.email = "  CLEAN@EXAMPLE.TEST "
    user.full_clean()
    assert user.email == "clean@example.test"


@pytest.mark.parametrize("email", ["", "bad email"])
def test_model_full_clean_rejects_invalid_email(email):
    user = User(username="invalid", email=email)
    with pytest.raises(ValidationError):
        user.full_clean()


def test_database_enforces_email_uniqueness_after_normalisation(customer):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("collision", " CUSTOMER@EXAMPLE.TEST ", TEST_PASSWORD)
    assert User.objects.count() == 1


@pytest.mark.parametrize("email", ["", "UPPER@example.test", " padded@example.test "])
def test_database_blocks_bulk_updates_bypassing_email_normalisation(customer, email):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=customer.pk).update(email=email)
    customer.refresh_from_db()
    assert customer.email == "customer@example.test"


@pytest.mark.parametrize("endpoint", ["register", "login"])
def test_auth_posts_require_csrf(endpoint, registration_data, customer):
    client = Client(enforce_csrf_checks=True)
    path = reverse(f"accounts:{endpoint}")
    data = (
        registration_data
        if endpoint == "register"
        else {"username": customer.username, "password": TEST_PASSWORD}
    )
    if endpoint == "register":
        data = {**data, "email": "csrf@example.test"}
    before = User.objects.count()
    assert client.post(path, data).status_code == 403
    assert client.post(path, data, HTTP_X_CSRFTOKEN="incorrect").status_code == 403
    assert User.objects.count() == before
    client.get(path)
    token = client.cookies["csrftoken"].value
    assert client.post(path, data, HTTP_X_CSRFTOKEN=token).status_code == 302


def test_logout_requires_csrf_and_post(customer):
    client = Client(enforce_csrf_checks=True)
    client.force_login(customer)
    client.get(reverse("accounts:home"))
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).status_code == 403
    assert "_auth_user_id" in client.session
    token = client.cookies["csrftoken"].value
    assert client.post(reverse("accounts:logout"), HTTP_X_CSRFTOKEN=token).status_code == 302
    assert "_auth_user_id" not in client.session


def test_admin_posts_require_csrf(superuser):
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)
    assert (
        client.post(reverse("admin:accounts_user_add"), {"username": "blocked"}).status_code == 403
    )
    assert not User.objects.filter(username="blocked").exists()


def test_home_links_follow_permissions_and_escape_username(client, customer):
    customer.username = "<script>alert(1)</script>"
    customer.save(update_fields=["username"])
    client.force_login(customer)
    response = client.get(reverse("accounts:home"))
    html = response.content.decode()
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert 'href="/customer/"' in html
    assert 'href="/manager/"' not in html and 'href="/administration/"' not in html
    assert 'method="post" action="/accounts/logout/"' in html


@pytest.mark.django_db(transaction=True)
def test_forward_migrations_preserve_legacy_records_and_assign_customer():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    legacy_target = [("accounts", "0001_initial")]
    executor.migrate(legacy_target)
    apps = executor.loader.project_state(legacy_target).apps
    LegacyUser = apps.get_model("accounts", "User")
    from bookings.models import Booking
    from hotels.models import Hotel, Room

    try:
        hashed = make_password(TEST_PASSWORD)
        legacy_user = LegacyUser.objects.create(
            username="legacy", email=" Legacy@EXAMPLE.TEST ", password=hashed
        )
        hotel = Hotel.objects.create(
            name="Preserved Hotel", city="Warsaw", address="Test Street", stars=3
        )
        room = Room.objects.create(
            hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night="200.00"
        )
        booking = Booking.objects.create(
            user_id=legacy_user.pk,
            room=room,
            check_in="2027-01-01",
            check_out="2027-01-03",
            guests_count=2,
            price_per_night_snapshot="200.00",
            total_price="400.00",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        restored = User.objects.get(pk=legacy_user.pk)
        assert restored.email == "legacy@example.test" and restored.password == hashed
        assert restored.check_password(TEST_PASSWORD)
        assert list(restored.groups.values_list("name", flat=True)) == ["CUSTOMER"]
        booking.refresh_from_db()
        assert booking.user_id == restored.pk and booking.room_id == room.pk
        assert str(booking.total_price) == "400.00" and Hotel.objects.filter(pk=hotel.pk).exists()
    finally:
        MigrationExecutor(connection).migrate(latest)
        provision_roles()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "emails", [["", "valid@example.test"], [" Same@EXAMPLE.TEST ", "same@example.test"]]
)
def test_email_migration_stops_on_conflicts_without_deleting_records(emails):
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    legacy_target = [("accounts", "0001_initial")]
    executor.migrate(legacy_target)
    LegacyUser = executor.loader.project_state(legacy_target).apps.get_model("accounts", "User")
    try:
        for i, email in enumerate(emails):
            LegacyUser.objects.create(username=f"legacy{i}", email=email)
        with pytest.raises(RuntimeError, match="resolve blank or duplicate"):
            MigrationExecutor(connection).migrate(latest)
        assert (
            list(LegacyUser.objects.order_by("username").values_list("email", flat=True)) == emails
        )
    finally:
        LegacyUser.objects.all().delete()
        MigrationExecutor(connection).migrate(latest)
        provision_roles()


def test_role_data_migration_preserves_existing_role_and_staff(customer, superuser):
    module = import_module("accounts.migrations.0003_existing_customer_role")
    from django.apps import apps

    customer.groups.set([Group.objects.get(name="MANAGER")])
    module.assign_existing_customers(apps, connection.schema_editor())
    assert list(customer.groups.values_list("name", flat=True)) == ["MANAGER"]
    assert not superuser.groups.exists()
