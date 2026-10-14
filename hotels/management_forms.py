from django import forms

from .forms import HotelFilterForm
from .models import Hotel, Room

ACTIVE_CHOICES = [("", "Any status"), ("active", "Active"), ("inactive", "Inactive")]


class HotelManagementFilterForm(HotelFilterForm):
    active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)


class RoomManagementFilterForm(forms.Form):
    hotel = forms.ModelChoiceField(
        queryset=Hotel.objects.order_by("name", "pk"), required=False, empty_label="All hotels"
    )
    number = forms.CharField(required=False, max_length=20)
    room_type = forms.ChoiceField(
        required=False, choices=[("", "Any type")] + list(Room.RoomType.choices)
    )
    active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)


class HotelForm(forms.ModelForm):
    class Meta:
        model = Hotel
        fields = ("name", "city", "address", "description", "stars", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = (
            "hotel",
            "number",
            "room_type",
            "capacity",
            "price_per_night",
            "description",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        labels = {"price_per_night": "Price per night (PLN)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hotel"].queryset = Hotel.objects.order_by("name", "pk")
        if self.instance.pk:
            self.fields["hotel"].disabled = True
            self.fields["hotel"].help_text = "An existing room stays in its original hotel."
