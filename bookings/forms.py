from django import forms

from .models import BlockedDate, SessionType, WorkingHours


class SignInForm(forms.Form):
    email = forms.EmailField(max_length=254)
    password = forms.CharField(widget=forms.PasswordInput)


class BookingForm(forms.Form):
    full_name = forms.CharField(max_length=100, label="Full name")
    email = forms.EmailField(max_length=254, label="Email address")
    location = forms.CharField(max_length=200, label="Shoot location")
    notes = forms.CharField(
        max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )


class SessionForm(forms.ModelForm):
    class Meta:
        model = SessionType
        fields = ["name", "description", "duration", "price", "active"]
        labels = {"duration": "Duration (minutes)", "price": "Price (AUD)"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class HoursForm(forms.ModelForm):
    class Meta:
        model = WorkingHours
        fields = ["weekday", "start", "end"]
        widgets = {
            "start": forms.TimeInput(attrs={"type": "time"}),
            "end": forms.TimeInput(attrs={"type": "time"}),
        }


class BlockForm(forms.ModelForm):
    class Meta:
        model = BlockedDate
        fields = ["date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
