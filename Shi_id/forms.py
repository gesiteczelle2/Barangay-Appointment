import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Appointment,
    Barangay,
    CityMunicipality,
    PurokSitio,
    ResidentProfile,
    ScheduleClosure,
    Service,
)
from .scheduling import ACTIVE_STATUSES, SLOT_CHOICES, SLOT_STARTS, is_business_day


def normalize_person_name(value):
    value = ' '.join(value.split())
    parts = re.split(r"([ '-])", value)
    return ''.join(
        part.lower().capitalize() if part not in {' ', "'", '-'} else part
        for part in parts
    )


class InitialAdminPasswordChangeForm(PasswordChangeForm):
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password1')
        if password and self.user.check_password(password):
            self.add_error(
                'new_password1',
                'Choose a new password different from the initial password.',
            )
        return cleaned_data


class ResidentRegistrationForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=150,
        label='First name',
        widget=forms.TextInput(attrs={'autocomplete': 'given-name', 'data-name-field': 'true'}),
    )
    middle_name = forms.CharField(
        max_length=100,
        required=False,
        label='Middle name (optional)',
        widget=forms.TextInput(attrs={'autocomplete': 'additional-name', 'data-name-field': 'true'}),
    )
    last_name = forms.CharField(
        max_length=150,
        label='Last name',
        widget=forms.TextInput(attrs={'autocomplete': 'family-name', 'data-name-field': 'true'}),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )
    contact_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel'}),
    )
    city_municipality = forms.ModelChoiceField(
        queryset=CityMunicipality.objects.none(),
        label='City / Municipality',
    )
    barangay = forms.ModelChoiceField(queryset=Barangay.objects.none())
    purok = forms.ModelChoiceField(queryset=PurokSitio.objects.none(), label='Purok / Sitio')
    street_address = forms.CharField(
        max_length=200,
        label='Street / House Number',
        widget=forms.TextInput(attrs={'autocomplete': 'street-address'}),
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = (
            'username',
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'contact_number',
            'city_municipality',
            'barangay',
            'purok',
            'street_address',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cities = CityMunicipality.objects.filter(is_active=True)
        self.fields['city_municipality'].queryset = cities
        selected_city = self.data.get('city_municipality') or self.initial.get('city_municipality')
        if not self.is_bound and not selected_city and cities.count() == 1:
            selected_city = cities.first().pk
            self.initial['city_municipality'] = selected_city
        if selected_city:
            self.fields['barangay'].queryset = Barangay.objects.filter(
                city_municipality_id=selected_city,
                city_municipality__is_active=True,
                is_active=True,
            )
        selected_barangay = self.data.get('barangay') or self.initial.get('barangay')
        if selected_barangay:
            self.fields['purok'].queryset = PurokSitio.objects.filter(
                barangay_id=selected_barangay,
                barangay__is_active=True,
                barangay__city_municipality__is_active=True,
                is_active=True,
            ).order_by('pk')
        for name in ('username', 'password1', 'password2'):
            self.fields[name].widget.attrs.setdefault('autocomplete', 'off' if name == 'username' else 'new-password')

    def clean_first_name(self):
        return normalize_person_name(self.cleaned_data['first_name'])

    def clean_middle_name(self):
        return normalize_person_name(self.cleaned_data.get('middle_name', ''))

    def clean_last_name(self):
        return normalize_person_name(self.cleaned_data['last_name'])

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def clean_contact_number(self):
        contact = ' '.join(self.cleaned_data['contact_number'].split())
        if not re.fullmatch(r'[0-9+().\-\s]{7,20}', contact):
            raise forms.ValidationError('Enter a valid contact number.')
        return contact

    def clean_street_address(self):
        address = ' '.join(self.cleaned_data['street_address'].split())
        if not address:
            raise forms.ValidationError('Enter a street or house number.')
        return address

    def clean(self):
        cleaned_data = super().clean()
        city = cleaned_data.get('city_municipality')
        barangay = cleaned_data.get('barangay')
        purok = cleaned_data.get('purok')
        if city and barangay and barangay.city_municipality_id != city.pk:
            self.add_error('barangay', 'Choose a barangay belonging to the selected city or municipality.')
        elif city and not barangay and self.data.get('barangay'):
            submitted_barangay = Barangay.objects.filter(
                pk=self.data.get('barangay'),
                is_active=True,
                city_municipality__is_active=True,
            ).first()
            if submitted_barangay and submitted_barangay.city_municipality_id != city.pk:
                self.add_error('barangay', 'Choose a barangay belonging to the selected city or municipality.')
        if barangay and purok and purok.barangay_id != barangay.pk:
            self.add_error('purok', 'Choose a purok or sitio belonging to the selected barangay.')
        elif barangay and not purok and self.data.get('purok'):
            submitted_purok = PurokSitio.objects.filter(
                pk=self.data.get('purok'),
                is_active=True,
                barangay__is_active=True,
                barangay__city_municipality__is_active=True,
            ).first()
            if submitted_purok and submitted_purok.barangay_id != barangay.pk:
                self.add_error('purok', 'Choose a purok or sitio belonging to the selected barangay.')
        return cleaned_data

    def save(self, commit=True):
        with transaction.atomic():
            user = super().save(commit=False)
            user.first_name = self.cleaned_data['first_name']
            user.last_name = self.cleaned_data['last_name']
            user.email = self.cleaned_data['email']
            if commit:
                user.save()
                ResidentProfile.objects.create(
                    user=user,
                    middle_name=self.cleaned_data['middle_name'],
                    contact_number=self.cleaned_data['contact_number'],
                    purok=self.cleaned_data['purok'],
                    street_address=self.cleaned_data['street_address'],
                )
        return user


class AppointmentForm(forms.ModelForm):
    purpose = forms.CharField(
        required=False,
        max_length=200,
        label='Additional details',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Additional details (required for Other Barangay Services)',
        }),
    )
    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        empty_label='Select a service',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    time = forms.TimeField(
        widget=forms.Select(
            choices=[('', 'Select a time slot'), *SLOT_CHOICES],
            attrs={'class': 'form-control'},
        )
    )

    class Meta:
        model = Appointment
        fields = ['service', 'resident_name', 'contact_number', 'date', 'time', 'purpose']
        widgets = {
            'resident_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter resident name',
                'autocomplete': 'name',
                'data-name-field': 'true',
            }),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter contact number',
                'autocomplete': 'tel',
            }),
            'date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['service'].queryset = Service.objects.filter(
            is_active=True,
        ).filter(
            Q(requires_configuration=False) | Q(is_configured=True),
        )

    def clean(self):
        cleaned_data = super().clean()
        day = cleaned_data.get('date')
        start = cleaned_data.get('time')
        service = cleaned_data.get('service')
        purpose = cleaned_data.get('purpose')
        if service and service.requires_description and not purpose:
            self.add_error('purpose', 'Describe the service you need.')
        if not day or not start:
            return cleaned_data

        today = timezone.localdate()
        if day < today:
            self.add_error('date', 'Past dates cannot be booked.')
            return cleaned_data
        if not is_business_day(day):
            self.add_error('date', 'Appointments are available Monday to Friday only.')
            return cleaned_data
        if start not in SLOT_STARTS:
            self.add_error('time', 'Select one of the available 30-minute sessions.')
            return cleaned_data
        if day == today and start <= timezone.localtime().time().replace(tzinfo=None):
            self.add_error('time', 'Past appointment times cannot be booked.')
            return cleaned_data

        closures = ScheduleClosure.objects.filter(date=day)
        if closures.filter(time__isnull=True).exists():
            self.add_error('date', 'This date has been manually closed by staff.')
            return cleaned_data
        if closures.filter(time=start).exists():
            self.add_error('time', 'This time slot has been manually closed by staff.')
        if Appointment.objects.filter(
            date=day,
            time=start,
            status__in=ACTIVE_STATUSES,
        ).exists():
            self.add_error('time', 'This time slot has already been taken. Please choose another.')
        return cleaned_data

    def clean_resident_name(self):
        return normalize_person_name(self.cleaned_data['resident_name'])

    def clean_contact_number(self):
        contact = ' '.join(self.cleaned_data['contact_number'].split())
        if not re.fullmatch(r'[0-9+().\-\s]{7,20}', contact):
            raise forms.ValidationError('Enter a valid contact number.')
        return contact

    def clean_purpose(self):
        purpose = ' '.join(self.cleaned_data['purpose'].split())
        return purpose

    def save(self, commit=True):
        appointment = super().save(commit=False)
        appointment.service_name_snapshot = appointment.service.name
        if not appointment.purpose:
            appointment.purpose = appointment.service.name
        if commit:
            appointment.save()
            self.save_m2m()
        return appointment


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = (
            'name',
            'description',
            'is_active',
            'requires_configuration',
            'is_configured',
            'requires_description',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())


class AppointmentReportFilterForm(forms.Form):
    STATUS_CHOICES = (
        ('', 'All statuses'),
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    )
    PERIOD_CHOICES = (
        ('day', 'Day'),
        ('week', 'Week'),
        ('month', 'Month'),
    )

    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    service = forms.ModelChoiceField(
        queryset=Service.objects.all(),
        required=False,
        empty_label='All services',
    )
    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)
    period = forms.ChoiceField(choices=PERIOD_CHOICES, initial='day')
    resident = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label='All residents',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['resident'].queryset = get_user_model().objects.filter(
            resident_profile__isnull=False,
        ).order_by('last_name', 'first_name', 'username')

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and start_date > end_date:
            self.add_error('end_date', 'End date must be on or after the start date.')
        return cleaned_data


class ResidentProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, label='First name')
    middle_name = forms.CharField(max_length=100, required=False, label='Middle name')
    last_name = forms.CharField(max_length=150, label='Last name')
    email = forms.EmailField()
    city_municipality = forms.ModelChoiceField(queryset=CityMunicipality.objects.none())
    barangay = forms.ModelChoiceField(queryset=Barangay.objects.none())

    class Meta:
        model = ResidentProfile
        fields = (
            'contact_number',
            'purok',
            'street_address',
        )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        current_purok = self.instance.purok if self.instance and self.instance.pk else None
        current_barangay = current_purok.barangay if current_purok else None
        current_city = current_barangay.city_municipality if current_barangay else None
        cities = CityMunicipality.objects.filter(is_active=True)
        if current_city:
            cities = (cities | CityMunicipality.objects.filter(pk=current_city.pk)).distinct()
        self.fields['city_municipality'].queryset = cities
        selected_city = self.data.get('city_municipality') if self.is_bound else (
            current_city.pk if current_city else None
        )
        barangays = Barangay.objects.filter(
            city_municipality_id=selected_city,
            city_municipality__is_active=True,
            is_active=True,
        )
        if current_barangay:
            barangays = (barangays | Barangay.objects.filter(pk=current_barangay.pk)).distinct()
        self.fields['barangay'].queryset = barangays
        puroks = PurokSitio.objects.filter(
            barangay_id=self.data.get('barangay') if self.is_bound else (
                current_barangay.pk if current_barangay else None
            ),
            is_active=True,
            barangay__is_active=True,
            barangay__city_municipality__is_active=True,
        ).order_by('pk')
        if current_purok:
            puroks = (puroks | PurokSitio.objects.filter(pk=current_purok.pk)).distinct()
        self.fields['purok'].queryset = puroks
        self.fields['first_name'].initial = user.first_name
        self.fields['middle_name'].initial = self.instance.middle_name
        self.fields['last_name'].initial = user.last_name
        self.fields['email'].initial = user.email
        self.order_fields((
            'first_name',
            'middle_name',
            'last_name',
            'email',
            'contact_number',
            'city_municipality',
            'barangay',
            'purok',
            'street_address',
        ))

    def clean_first_name(self):
        return normalize_person_name(self.cleaned_data['first_name'])

    def clean_middle_name(self):
        return normalize_person_name(self.cleaned_data.get('middle_name', ''))

    def clean_last_name(self):
        return normalize_person_name(self.cleaned_data['last_name'])

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if get_user_model().objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def clean_contact_number(self):
        contact = ' '.join(self.cleaned_data['contact_number'].split())
        if not re.fullmatch(r'[0-9+().\-\s]{7,20}', contact):
            raise forms.ValidationError('Enter a valid contact number.')
        return contact

    def clean(self):
        cleaned_data = super().clean()
        city = cleaned_data.get('city_municipality')
        barangay = cleaned_data.get('barangay')
        purok = cleaned_data.get('purok')
        if city and barangay and barangay.city_municipality_id != city.pk:
            self.add_error('barangay', 'Choose a barangay belonging to the selected city or municipality.')
        if barangay and purok and purok.barangay_id != barangay.pk:
            self.add_error('purok', 'Choose a purok or sitio belonging to the selected barangay.')
        return cleaned_data

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.middle_name = self.cleaned_data['middle_name']
        if commit:
            with transaction.atomic():
                self.user.first_name = self.cleaned_data['first_name']
                self.user.last_name = self.cleaned_data['last_name']
                self.user.email = self.cleaned_data['email']
                self.user.save(update_fields=['first_name', 'last_name', 'email'])
                profile.save()
        return profile


class ScheduleClosureForm(forms.ModelForm):
    class Meta:
        model = ScheduleClosure
        fields = ['date', 'time', 'reason']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time': forms.Select(
                choices=[('', 'Close the entire day'), *SLOT_CHOICES],
                attrs={'class': 'form-control'},
            ),
            'reason': forms.TextInput(attrs={
                'class': 'form-control',
                'maxlength': '300',
                'placeholder': 'Reason for closing',
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        day = cleaned_data.get('date')
        start = cleaned_data.get('time')
        if day and not is_business_day(day):
            self.add_error('date', 'Only scheduled weekdays can be closed.')
        if start and start not in SLOT_STARTS:
            self.add_error('time', 'Select a valid scheduled time slot.')
        if day and ScheduleClosure.objects.filter(date=day, time__isnull=True).exists():
            self.add_error('date', 'This entire day is already closed.')
        if day and start and ScheduleClosure.objects.filter(date=day, time=start).exists():
            self.add_error('time', 'This time slot is already closed.')
        return cleaned_data


class AppointmentAdminForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        self.instance.status = cleaned_data.get('status', self.instance.status)
        self.instance.clean()
        return cleaned_data
