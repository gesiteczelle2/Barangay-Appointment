from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    ]

    appointment_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    service = models.ForeignKey(
        'Service',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    service_name_snapshot = models.CharField(max_length=150, blank=True)
    resident = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    resident_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=20)
    resident_address = models.CharField(max_length=500, blank=True)
    date = models.DateField()
    time = models.TimeField()
    purpose = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='Pending'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['date', 'time'],
                condition=Q(status__in=['Pending', 'Approved']),
                name='uniq_active_appointment_slot',
            ),
        ]
        ordering = ['-date', '-time']

    def save(self, *args, **kwargs):
        if self.pk is None and not self.appointment_id:
            with transaction.atomic():
                super().save(*args, **kwargs)
                self.appointment_id = f'BRGY-{self.date.year}-{self.pk:04d}'
                super().save(using=self._state.db, update_fields=['appointment_id'])
            return
        super().save(*args, **kwargs)

    def clean(self):
        old_appointment = None
        if self.pk:
            old_appointment = Appointment.objects.filter(pk=self.pk).values(
                'status', 'date', 'time'
            ).first()
        allowed_transitions = {
            'Pending': {'Pending', 'Approved', 'Cancelled'},
            'Approved': {'Approved', 'Completed', 'Cancelled'},
            'Completed': {'Completed'},
            'Cancelled': {'Cancelled'},
        }
        if old_appointment and self.status not in allowed_transitions.get(old_appointment['status'], set()):
            raise ValidationError({'status': 'This appointment status transition is not allowed.'})

        if self.date is None or self.time is None:
            return
        slot_changed = (
            old_appointment is None
            or self.date != old_appointment['date']
            or self.time != old_appointment['time']
        )
        if not slot_changed or self.status not in {'Pending', 'Approved'}:
            return

        from .scheduling import ACTIVE_STATUSES, SLOT_STARTS, is_business_day

        if not is_business_day(self.date):
            raise ValidationError({'date': 'Appointments are available Monday to Friday only.'})
        if self.time not in SLOT_STARTS:
            raise ValidationError({'time': 'Select one of the available 30-minute sessions.'})
        current = timezone.localtime()
        if self.date < current.date() or (
            self.date == current.date() and self.time <= current.time().replace(tzinfo=None)
        ):
            raise ValidationError({'date': 'Past appointment dates and times cannot be booked.'})
        closures = ScheduleClosure.objects.filter(date=self.date)
        if closures.filter(time__isnull=True).exists():
            raise ValidationError({'date': 'This date has been manually closed by staff.'})
        if closures.filter(time=self.time).exists():
            raise ValidationError({'time': 'This time slot has been manually closed by staff.'})
        conflicting_appointments = Appointment.objects.filter(
            date=self.date,
            time=self.time,
            status__in=ACTIVE_STATUSES,
        )
        if self.pk:
            conflicting_appointments = conflicting_appointments.exclude(pk=self.pk)
        if conflicting_appointments.exists():
            raise ValidationError({'time': 'This time slot has already been taken.'})

    def __str__(self):
        return self.appointment_id or f'Appointment {self.pk}'

    @property
    def end_time(self):
        return (datetime.combine(self.date, self.time) + timedelta(minutes=30)).time()


class CityMunicipality(models.Model):
    name = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    psgc_code = models.CharField(max_length=12, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'cities and municipalities'

    def __str__(self):
        return f'{self.name}, {self.province}'


class Service(models.Model):
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    requires_configuration = models.BooleanField(default=False)
    is_configured = models.BooleanField(default=True)
    requires_description = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                name='uniq_service_name_case_insensitive',
            ),
        ]

    def clean(self):
        self.name = self.name.strip()
        if self.requires_configuration and self.is_active and not self.is_configured:
            raise ValidationError({
                'is_active': 'Configure this service before making it available to residents.',
            })

    def __str__(self):
        return self.name


class Barangay(models.Model):
    city_municipality = models.ForeignKey(
        CityMunicipality,
        on_delete=models.PROTECT,
        related_name='barangays',
    )
    name = models.CharField(max_length=100)
    psgc_code = models.CharField(max_length=12, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'barangays'

    def __str__(self):
        return self.name


class PurokSitio(models.Model):
    barangay = models.ForeignKey(
        Barangay,
        on_delete=models.PROTECT,
        related_name='puroks',
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['barangay__name', 'name']
        verbose_name = 'purok or sitio'
        verbose_name_plural = 'puroks and sitios'
        constraints = [
            models.UniqueConstraint(
                Lower(Trim('name')),
                F('barangay'),
                name='uniq_purok_name_per_barangay',
            ),
        ]

    def clean(self):
        self.name = self.name.strip()

    def __str__(self):
        return f'{self.name} — {self.barangay}'


class ResidentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='resident_profile',
    )
    middle_name = models.CharField(max_length=100, blank=True)
    contact_number = models.CharField(max_length=20)
    purok = models.ForeignKey(
        PurokSitio,
        on_delete=models.PROTECT,
        related_name='residents',
    )
    street_address = models.CharField(max_length=200)

    class Meta:
        ordering = ['user__last_name', 'user__first_name']

    @property
    def full_name(self):
        return ' '.join(
            name for name in (
                self.user.first_name,
                self.middle_name,
                self.user.last_name,
            ) if name
        )

    @property
    def barangay(self):
        return self.purok.barangay

    @property
    def city_municipality(self):
        return self.purok.barangay.city_municipality

    @property
    def complete_address(self):
        return ', '.join((
            self.street_address,
            self.purok.name,
            self.purok.barangay.name,
            self.purok.barangay.city_municipality.name,
            self.purok.barangay.city_municipality.province,
        ))

    def __str__(self):
        return self.full_name


class UserSecuritySettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='security_settings',
    )
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return f'Security settings for {self.user}'


class ScheduleClosure(models.Model):
    date = models.DateField()
    time = models.TimeField(null=True, blank=True)
    reason = models.CharField(max_length=300)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='schedule_closures',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['date'],
                condition=Q(time__isnull=True),
                name='uniq_closed_schedule_day',
            ),
            models.UniqueConstraint(
                fields=['date', 'time'],
                condition=Q(time__isnull=False),
                name='uniq_closed_schedule_slot',
            ),
        ]
        ordering = ['date', 'time']

    def __str__(self):
        return f'{self.date} - {self.time or "All day"}'
