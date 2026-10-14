from django import forms

from .models import Booking


class BookingFilterForm(forms.Form):
    status = forms.ChoiceField(
        required=False, choices=[("", "Any status")] + list(Booking.Status.choices)
    )
    search = forms.CharField(label="Hotel or room", required=False, max_length=150)
    from_date = forms.DateField(
        label="Check-in from",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    to_date = forms.DateField(
        label="Check-in through",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        data = super().clean()
        if data.get("from_date") and data.get("to_date") and data["to_date"] < data["from_date"]:
            self.add_error("to_date", "End date must be on or after the start date.")
        return data


class ManagementBookingFilterForm(BookingFilterForm):
    customer = forms.CharField(label="Customer username", required=False, max_length=150)
