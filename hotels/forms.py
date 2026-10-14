from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Hotel, Room


class HotelFilterForm(forms.Form):
    name = forms.CharField(label="Hotel name", required=False, max_length=150)
    city = forms.CharField(required=False, max_length=100)
    stars = forms.TypedChoiceField(
        required=False,
        coerce=int,
        empty_value=None,
        choices=[("", "Any rating")] + [(number, f"{number} stars") for number in range(1, 6)],
    )


class RoomFilterForm(forms.Form):
    hotel = forms.ModelChoiceField(
        queryset=Hotel.objects.none(), required=False, empty_label="All hotels"
    )
    room_type = forms.ChoiceField(
        required=False, choices=[("", "Any type")] + list(Room.RoomType.choices)
    )
    capacity = forms.IntegerField(
        label="Minimum capacity", required=False, min_value=1, max_value=32767
    )
    min_price = forms.DecimalField(
        label="Minimum price (PLN)",
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
    )
    max_price = forms.DecimalField(
        label="Maximum price (PLN)",
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
    )
    check_in = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    check_out = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hotel"].queryset = Hotel.objects.filter(is_active=True).order_by(
            "name", "city", "pk"
        )

    def clean(self):
        data = super().clean()
        minimum = data.get("min_price")
        maximum = data.get("max_price")
        if minimum is not None and maximum is not None and maximum < minimum:
            self.add_error("max_price", "Maximum price must be at least the minimum price.")
        check_in = data.get("check_in")
        check_out = data.get("check_out")
        if check_in and not check_out and "check_out" not in self.errors:
            self.add_error("check_out", "Enter a check-out date to search availability.")
        if check_out and not check_in and "check_in" not in self.errors:
            self.add_error("check_in", "Enter a check-in date to search availability.")
        if check_in and check_in < timezone.localdate():
            self.add_error("check_in", "Check-in must be today or later.")
        if check_in and check_out and check_out <= check_in:
            self.add_error("check_out", "Check-out must be after check-in.")
        return data
