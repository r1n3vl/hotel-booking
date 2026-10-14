import io
import json
import os
import runpy
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from hotels.models import Hotel, Room

pytestmark = pytest.mark.django_db
PASSWORD = "University-demo-test-only-924!"


def seed():
    output = io.StringIO()
    call_command("seed_demo", stdout=output)
    return output.getvalue()


def records():
    return {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in [User, Hotel, Room, Booking]
    }


def test_health_checks_postgresql_without_login(client):
    response = client.get(reverse("health"))
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_health_does_not_disclose_database_errors(client):
    with patch(
        "accounts.health.connection.cursor",
        side_effect=OperationalError("private connection details"),
    ):
        response = client.get(reverse("health"))
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    assert b"private" not in response.content


def test_health_is_read_only(client):
    assert client.post(reverse("health")).status_code == 405


def test_seed_creates_roles_inventory_and_correct_booking(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    output = seed()
    assert PASSWORD not in output
    assert User.objects.count() == 3 and Hotel.objects.count() == 1
    assert Room.objects.count() == 3 and Booking.objects.count() == 1
    for role in ["CUSTOMER", "MANAGER", "ADMIN"]:
        user = User.objects.get(username=f"sample_{role.lower()}")
        assert user.check_password(PASSWORD)
        assert list(user.groups.values_list("name", flat=True)) == [role]
        assert user.is_staff == (role == "ADMIN") and not user.is_superuser
    booking = Booking.objects.get()
    assert booking.user.username == "sample_customer" and booking.guests_count == 1
    assert booking.check_in == timezone.localdate() + timedelta(days=7)
    assert (booking.check_out - booking.check_in).days == 2
    assert booking.total_price == 240 and booking.price_per_night_snapshot == 120
    assert booking.status == "PENDING"


def test_seed_is_idempotent_and_preserves_changed_password_role_price_and_booking(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    seed()
    user = User.objects.get(username="sample_customer")
    user.set_password("Changed-test-only-password-925!")
    user.save()
    user.groups.set([Group.objects.get(name="MANAGER")])
    room = Room.objects.get(number="D101")
    room.price_per_night = "130.00"
    room.save()
    hotel = Hotel.objects.get()
    hotel.stars = 5
    hotel.save()
    before = records()
    monkeypatch.setenv("DEMO_PASSWORD", "Different-test-only-password-926!")
    seed()
    assert records() == before


@pytest.mark.parametrize("password", [None, "12345678"])
def test_seed_requires_explicit_strong_password_and_changes_nothing(monkeypatch, password):
    if password is None:
        monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("DEMO_PASSWORD", password)
    before = records()
    with pytest.raises(CommandError):
        seed()
    assert records() == before


def test_seed_refuses_user_collision_and_rolls_back_earlier_creations(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    User.objects.create_user("sample_manager", "existing@example.test")
    before = records()
    with pytest.raises(CommandError, match="another account"):
        seed()
    assert records() == before


def test_seed_refuses_email_collision(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    User.objects.create_user("existing", "sample_customer@example.test")
    before = records()
    with pytest.raises(CommandError, match="safely"):
        seed()
    assert records() == before


def test_seed_requires_migrated_roles(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    Group.objects.filter(name="ADMIN").delete()
    with pytest.raises(CommandError, match="migrated roles"):
        seed()
    assert not User.objects.exists() and not Hotel.objects.exists()


def test_seed_keeps_unrelated_records(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    user = User.objects.create_user("existing", "existing@example.test")
    hotel = Hotel.objects.create(
        name="Existing Hotel", city="Warsaw", address="Existing Street", stars=3
    )
    room = Room.objects.create(
        hotel=hotel, number="101", room_type="SINGLE", capacity=1, price_per_night="100.00"
    )
    before = {
        "user": User.objects.get(pk=user.pk).__dict__.copy(),
        "hotel": Hotel.objects.get(pk=hotel.pk).__dict__.copy(),
        "room": Room.objects.get(pk=room.pk).__dict__.copy(),
    }
    seed()
    for key, model, pk in [
        ("user", User, user.pk),
        ("hotel", Hotel, hotel.pk),
        ("room", Room, room.pk),
    ]:
        after = model.objects.get(pk=pk).__dict__.copy()
        after.pop("_state")
        original = before[key]
        original.pop("_state")
        assert after == original


def test_environment_configures_http_and_https_modes():
    environment = os.environ.copy()
    environment.update(
        DJANGO_DEBUG="false",
        DJANGO_ALLOWED_HOSTS="localhost, example.test ,127.0.0.1",
        DJANGO_CSRF_TRUSTED_ORIGINS="https://example.test",
        DJANGO_SECURE_COOKIES="true",
        DJANGO_SECURE_SSL_REDIRECT="true",
        DJANGO_HSTS_SECONDS="3600",
    )
    code = "import json,config.settings as s; print(json.dumps([s.DEBUG,s.ALLOWED_HOSTS,s.CSRF_TRUSTED_ORIGINS,s.CSRF_COOKIE_SECURE,s.SESSION_COOKIE_SECURE,s.SECURE_SSL_REDIRECT,s.SECURE_HSTS_SECONDS]))"
    response = subprocess.run(
        [sys.executable, "-c", code],
        cwd=settings.BASE_DIR,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(response.stdout) == [
        False,
        ["localhost", "example.test", "127.0.0.1"],
        ["https://example.test"],
        True,
        True,
        True,
        3600,
    ]


def test_container_start_migrates_collects_and_executes_gunicorn(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    with patch("subprocess.run") as run, patch("os.execvp") as execute:
        runpy.run_path(str(Path(settings.BASE_DIR) / "docker/start.py"))
    assert run.call_args_list[0].args[0] == [sys.executable, "manage.py", "migrate", "--noinput"]
    assert run.call_args_list[1].args[0] == [
        sys.executable,
        "manage.py",
        "collectstatic",
        "--noinput",
    ]
    assert all(call.kwargs["check"] for call in run.call_args_list)
    executable, args = execute.call_args.args
    assert executable == "gunicorn" and "config.wsgi:application" in args
    assert args[args.index("--workers") + 1] == "3"


def test_container_start_stops_on_migration_error():
    with (
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "migrate")) as run,
        patch("os.execvp") as execute,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            runpy.run_path(str(Path(settings.BASE_DIR) / "docker/start.py"))
    assert run.call_count == 1 and not execute.called
