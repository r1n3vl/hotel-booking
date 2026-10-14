from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.access import permission_required
from hotels.views import catalogue_context

from .list_forms import BookingFilterForm, ManagementBookingFilterForm
from .models import Booking
from .services import change_booking_status


def visible_bookings(user):
    records = Booking.objects.select_related("room__hotel", "user")
    return records if user.has_perm("bookings.change_booking") else records.filter(user=user)


def booking_list(request, all_bookings=False):
    form_class = ManagementBookingFilterForm if all_bookings else BookingFilterForm
    form = form_class(request.GET)
    records = Booking.objects.select_related("room__hotel", "user").order_by("-created_at", "-pk")
    if not all_bookings:
        records = records.filter(user=request.user)
    if form.is_valid():
        data = form.cleaned_data
        if data["status"]:
            records = records.filter(status=data["status"])
        if data["search"]:
            records = records.filter(
                Q(room__hotel__name__icontains=data["search"])
                | Q(room__number__icontains=data["search"])
            )
        if data["from_date"]:
            records = records.filter(check_in__gte=data["from_date"])
        if data["to_date"]:
            records = records.filter(check_in__lte=data["to_date"])
        if all_bookings and data["customer"]:
            records = records.filter(user__username__icontains=data["customer"])
    else:
        records = records.none()
    context = catalogue_context(request, form, records)
    context["all_bookings"] = all_bookings
    return render(request, "bookings/list.html", context)


@permission_required("bookings.view_booking")
@require_GET
def my_bookings(request):
    return booking_list(request)


@permission_required("bookings.change_booking")
@require_GET
def all_bookings(request):
    return booking_list(request, all_bookings=True)


def transition(request, pk, status):
    booking = get_object_or_404(visible_bookings(request.user), pk=pk)
    try:
        change_booking_status(actor=request.user, booking_id=booking.pk, status=status)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request,
            "Booking confirmed." if status == Booking.Status.CONFIRMED else "Booking cancelled.",
        )
    return redirect("bookings:detail", pk=booking.pk)


@permission_required("bookings.view_booking")
@require_POST
def cancel(request, pk):
    return transition(request, pk, Booking.Status.CANCELLED)


@permission_required("bookings.change_booking")
@require_POST
def confirm(request, pk):
    return transition(request, pk, Booking.Status.CONFIRMED)
