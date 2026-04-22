from django import forms
from django.contrib.auth.models import User
from .models import Teller

class TellerSignUpForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'id': 'password-field'}), 
        label="Password"
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'id': 'password-confirm-field'}), 
        label="Confirm Password"
    )
    counter_number = forms.IntegerField(min_value=1, label="Counter Number")

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password']

    def clean(self):
        # This method runs after the user clicks submit
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")

        # If both passwords were typed but they don't match, throw an error
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', "Passwords do not match. Please try again.")
            
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            Teller.objects.create(
                user=user, 
                counter_number=self.cleaned_data['counter_number']
            )
        return user