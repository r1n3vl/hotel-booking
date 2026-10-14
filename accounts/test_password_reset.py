import re
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.forms import NormalisedPasswordResetForm
from accounts.models import User
from bookings.models import Booking
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db
OLD_PASSWORD = "Old-test-only-passphrase-719!"
NEW_PASSWORD = "New-test-only-passphrase-831!"


@pytest.fixture
def user():
    user = User.objects.create_user("reset_customer", "reset@example.test", OLD_PASSWORD)
    user.groups.add(Group.objects.get(name="CUSTOMER"))
    return user


def token_url(user):
    return reverse(
        "accounts:password_reset_confirm",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": default_token_generator.make_token(user),
        },
    )


def password_data(**overrides):
    return {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD, **overrides}


def request_link(client, user, mailoutbox):
    response = client.post(reverse("accounts:password_reset"), {"email": user.email})
    assert response.url == reverse("accounts:password_reset_done")
    assert len(mailoutbox) == 1
    match = re.search(r"https?://[^\s]+", mailoutbox[0].body)
    assert match is not None
    return urlsplit(match.group()).path


def confirm_form(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert response.url.endswith("/set-password/")
    return response.url


def test_reset_link_from_login_and_public_pages(client):
    html = client.get(reverse("accounts:login")).content.decode()
    assert reverse("accounts:password_reset") in html
    for endpoint in ("password_reset", "password_reset_done", "password_reset_complete"):
        assert client.get(reverse(f"accounts:{endpoint}")).status_code == 200


def test_reset_form_normalises_email(user):
    form = NormalisedPasswordResetForm({"email": " RESET@EXAMPLE.TEST "})
    assert form.is_valid()
    assert form.cleaned_data["email"] == user.email


def test_request_sends_one_email_and_does_not_change_password_or_roles(client, user, mailoutbox):
    before_password = user.password
    path = request_link(client, user, mailoutbox)
    message = mailoutbox[0]
    assert message.to == [user.email]
    assert message.subject == "Reset your Hotel Booking password"
    assert "\n" not in message.subject
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert user.username in message.body
    assert OLD_PASSWORD not in message.body and NEW_PASSWORD not in message.body
    match = resolve(path)
    assert match.view_name == "accounts:password_reset_confirm"
    assert default_token_generator.check_token(user, match.kwargs["token"])
    user.refresh_from_db()
    assert user.password == before_password
    assert list(user.groups.values_list("name", flat=True)) == ["CUSTOMER"]


def test_known_unknown_inactive_and_unusable_accounts_have_same_response(client, user, mailoutbox):
    inactive = User.objects.create_user(
        "inactive_reset", "inactive_reset@example.test", OLD_PASSWORD, is_active=False
    )
    unusable = User.objects.create_user("unusable_reset", "unusable_reset@example.test")
    bodies = []
    for email in (user.email, "missing@example.test", inactive.email, unusable.email):
        response = client.post(reverse("accounts:password_reset"), {"email": email}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain == [(reverse("accounts:password_reset_done"), 302)]
        bodies.append(response.content)
    assert all(body == bodies[0] for body in bodies)
    assert len(mailoutbox) == 1


@pytest.mark.parametrize(
    "email", [" RESET@EXAMPLE.TEST ", "Reset@Example.Test", "reset@example.test"]
)
def test_normalised_email_request(client, user, mailoutbox, email):
    response = client.post(reverse("accounts:password_reset"), {"email": email})
    assert response.status_code == 302 and len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]


@pytest.mark.parametrize("email", ["", "not-an-email", "a" * 245 + "@example.test"])
def test_invalid_email_is_form_error_and_no_mail(client, mailoutbox, email):
    response = client.post(reverse("accounts:password_reset"), {"email": email})
    assert response.status_code == 200 and "email" in response.context["form"].errors
    assert len(mailoutbox) == 0


def test_full_email_reset_login_and_replay_protection(client, user, mailoutbox):
    link = request_link(client, user, mailoutbox)
    form_path = confirm_form(client, link)
    response = client.get(form_path)
    assert response.context["validlink"]
    response = client.post(form_path, password_data())
    assert response.url == reverse("accounts:password_reset_complete")
    assert "_auth_user_id" not in client.session
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) and not user.check_password(OLD_PASSWORD)
    replay = Client().get(link)
    assert replay.status_code == 200 and not replay.context["validlink"]
    assert b"invalid or expired" in replay.content
    assert (
        client.post(
            reverse("accounts:login"), {"username": user.username, "password": OLD_PASSWORD}
        ).status_code
        == 200
    )
    assert (
        client.post(
            reverse("accounts:login"), {"username": user.username, "password": NEW_PASSWORD}
        ).status_code
        == 302
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_password2": "Different-test-passphrase-741!"},
        {"new_password1": "123", "new_password2": "123"},
        {"new_password1": "1234567890", "new_password2": "1234567890"},
        {"new_password1": "", "new_password2": ""},
    ],
)
def test_invalid_new_password_does_not_change_account(client, user, overrides):
    form_path = confirm_form(client, token_url(user))
    response = client.post(form_path, password_data(**overrides))
    assert response.status_code == 200 and response.context["form"].errors
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


@pytest.mark.parametrize(
    "kind", ["invalid_token", "invalid_uid", "missing_user", "token_for_other_user"]
)
def test_bad_links_cannot_change_password(client, user, kind):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    if kind == "invalid_token":
        token = "invalid-token"
    elif kind == "invalid_uid":
        uid = "not-base64"
    elif kind == "missing_user":
        uid = urlsafe_base64_encode(force_bytes(999999))
    else:
        other = User.objects.create_user("other_reset", "other_reset@example.test", OLD_PASSWORD)
        uid = urlsafe_base64_encode(force_bytes(other.pk))
    path = reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    response = client.get(path)
    assert response.status_code == 200 and not response.context["validlink"]
    assert client.post(path, password_data()).status_code == 200
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


def test_token_expires_after_one_hour(client, user):
    assert settings.PASSWORD_RESET_TIMEOUT == 3600
    now = datetime(2026, 10, 14, 12, 0)
    with patch.object(default_token_generator, "_now", return_value=now):
        url = token_url(user)
    with patch.object(default_token_generator, "_now", return_value=now + timedelta(seconds=3601)):
        response = client.get(url)
    assert response.status_code == 200 and not response.context["validlink"]


def test_token_is_valid_before_timeout(client, user):
    now = datetime(2026, 10, 14, 12, 0)
    with patch.object(default_token_generator, "_now", return_value=now):
        url = token_url(user)
    with patch.object(default_token_generator, "_now", return_value=now + timedelta(seconds=3599)):
        assert client.get(url).status_code == 302


def test_direct_set_password_url_needs_token_session(client, user):
    path = reverse(
        "accounts:password_reset_confirm",
        kwargs={"uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": "set-password"},
    )
    assert not client.get(path).context["validlink"]
    client.post(path, password_data())
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


def test_token_cannot_be_used_in_another_session_after_redirect(user):
    first = Client()
    form_path = confirm_form(first, token_url(user))
    second = Client()
    assert not second.get(form_path).context["validlink"]
    second.post(form_path, password_data())
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)


def test_email_change_invalidates_old_token(client, user):
    path = token_url(user)
    user.email = "changed_reset@example.test"
    user.save(update_fields=["email"])
    assert not client.get(path).context["validlink"]


def test_reset_invalidates_existing_authenticated_sessions(user):
    logged_in = Client()
    logged_in.force_login(user)
    user.refresh_from_db()
    reset = Client()
    path = confirm_form(reset, token_url(user))
    assert reset.post(path, password_data()).status_code == 302
    assert logged_in.get(reverse("accounts:home")).status_code == 302
    assert "_auth_user_id" not in logged_in.session


def test_reset_preserves_roles_user_id_and_booking_data(client, user):
    hotel = Hotel.objects.create(name="Reset Hotel", city="Warsaw", address="Street", stars=3)
    room = Room.objects.create(
        hotel=hotel, number="101", room_type="DOUBLE", capacity=2, price_per_night=Decimal("200.00")
    )
    today = timezone.localdate()
    booking = Booking.objects.create(
        user=user,
        room=room,
        check_in=today + timedelta(days=5),
        check_out=today + timedelta(days=7),
        guests_count=2,
        price_per_night_snapshot="200.00",
        total_price="400.00",
    )
    before = list(Booking.objects.values())
    user_id = user.pk
    path = confirm_form(client, token_url(user))
    client.post(path, password_data())
    user.refresh_from_db()
    booking.refresh_from_db()
    assert user.pk == user_id and booking.user_id == user_id
    assert list(Booking.objects.values()) == before
    assert list(user.groups.values_list("name", flat=True)) == ["CUSTOMER"]
    assert not user.is_staff and not user.is_superuser


@pytest.mark.parametrize("role", ["CUSTOMER", "MANAGER", "ADMIN"])
def test_each_role_can_reset_without_role_change(client, user, mailoutbox, role):
    user.groups.set([Group.objects.get(name=role)])
    path = confirm_form(client, request_link(client, user, mailoutbox))
    client.post(path, password_data())
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert list(user.groups.values_list("name", flat=True)) == [role]


def test_superuser_can_reset_without_losing_flags(client, mailoutbox):
    root = User.objects.create_superuser("reset_root", "reset_root@example.test", OLD_PASSWORD)
    path = confirm_form(client, request_link(client, root, mailoutbox))
    client.post(path, password_data())
    root.refresh_from_db()
    assert root.is_staff and root.is_superuser and root.check_password(NEW_PASSWORD)


def test_reset_request_requires_csrf(user, mailoutbox):
    client = Client(enforce_csrf_checks=True)
    path = reverse("accounts:password_reset")
    assert client.post(path, {"email": user.email}).status_code == 403
    assert len(mailoutbox) == 0
    client.get(path)
    response = client.post(
        path, {"email": user.email}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
    )
    assert response.status_code == 302 and len(mailoutbox) == 1


def test_setting_new_password_requires_csrf(user):
    client = Client(enforce_csrf_checks=True)
    path = confirm_form(client, token_url(user))
    assert client.post(path, password_data()).status_code == 403
    user.refresh_from_db()
    assert user.check_password(OLD_PASSWORD)
    client.get(path)
    assert (
        client.post(
            path, password_data(), HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
        ).status_code
        == 302
    )
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)


def test_authenticated_user_can_request_without_echoing_email(client, user, mailoutbox):
    client.force_login(user)
    response = client.post(reverse("accounts:password_reset"), {"email": user.email}, follow=True)
    assert response.status_code == 200 and len(mailoutbox) == 1
    assert user.email.encode() not in response.content


def test_https_request_builds_https_link(client, user, mailoutbox):
    response = client.post(reverse("accounts:password_reset"), {"email": user.email}, secure=True)
    assert response.status_code == 302
    assert "https://testserver/accounts/reset/" in mailoutbox[0].body


def test_external_host_is_not_used_for_reset_email(client, user, mailoutbox):
    with override_settings(ALLOWED_HOSTS=["testserver"]):
        response = client.post(
            reverse("accounts:password_reset"), {"email": user.email}, HTTP_HOST="attacker.example"
        )
    assert response.status_code == 400 and len(mailoutbox) == 0


def test_reset_views_reject_unsupported_methods(client, user):
    assert client.put(reverse("accounts:password_reset")).status_code == 405
    path = confirm_form(client, token_url(user))
    assert client.put(path).status_code == 405
