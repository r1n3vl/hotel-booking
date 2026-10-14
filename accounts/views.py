from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .access import permission_required
from .forms import RegistrationForm


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(None, "Username or email is already registered. Please try another.")
        else:
            login(request, user)
            return redirect("accounts:home")
    return render(request, "accounts/register.html", {"form": form})


@login_required
def home(request):
    return render(request, "accounts/home.html")


@permission_required("accounts.access_customer_area")
def customer_area(request):
    return render(request, "accounts/area.html", {"area": "Customer"})


@permission_required("accounts.access_manager_area")
def manager_area(request):
    return render(request, "accounts/area.html", {"area": "Manager"})


@permission_required("accounts.access_admin_area")
def admin_area(request):
    return render(request, "accounts/area.html", {"area": "Administrator"})
