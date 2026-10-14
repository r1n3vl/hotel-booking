from django import forms

from .validation import validate_stay


class BookingForm(forms.Form):
    check_in = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    check_out = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    guests_count = forms.IntegerField(label="Guests", min_value=1, max_value=32767)

    def __init__(self, *args, room, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room
        self.fields["guests_count"].max_value = room.capacity
        self.fields["guests_count"].widget.attrs["max"] = room.capacity

    def clean(self):
        data = super().clean()
        if all(name in data for name in ("check_in", "check_out", "guests_count")):
            validate_stay(room=self.room, **data)
        return data
