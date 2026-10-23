from django.contrib import admin

from .forms import AppointmentAdminForm
from .models import (
    Appointment,
    Barangay,
    CityMunicipality,
    PurokSitio,
    ResidentProfile,
    ScheduleClosure,
    Service,
)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    form = AppointmentAdminForm
    list_display = (
        'appointment_id',
        'resident_name',
        'service_name_snapshot',
        'contact_number',
        'resident_address',
        'date',
        'time',
        'purpose',
        'status',
    )
    list_filter = ('status', 'date')
    search_fields = (
        'appointment_id',
        'resident_name',
        'contact_number',
        'resident_address',
        'service__name',
    )
    readonly_fields = ('appointment_id', 'created_at')


@admin.register(ScheduleClosure)
class ScheduleClosureAdmin(admin.ModelAdmin):
    list_display = ('date', 'time', 'reason', 'closed_by', 'created_at')
    list_filter = ('date',)
    search_fields = ('reason',)
    readonly_fields = ('created_at',)


@admin.register(CityMunicipality)
class CityMunicipalityAdmin(admin.ModelAdmin):
    list_display = ('name', 'province', 'psgc_code', 'is_active')
    list_filter = ('province', 'is_active')
    search_fields = ('name', 'province', 'psgc_code')
    list_editable = ('is_active',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Barangay)
class BarangayAdmin(admin.ModelAdmin):
    list_display = ('name', 'city_municipality', 'psgc_code', 'is_active')
    list_filter = ('city_municipality', 'is_active')
    search_fields = ('name', 'psgc_code', 'city_municipality__name')
    list_editable = ('is_active',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurokSitio)
class PurokSitioAdmin(admin.ModelAdmin):
    list_display = ('name', 'barangay', 'is_active')
    list_filter = ('barangay__city_municipality', 'barangay', 'is_active')
    search_fields = ('name', 'barangay__name')
    list_editable = ('is_active',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ResidentProfile)
class ResidentProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'contact_number', 'purok', 'street_address')
    list_filter = ('purok__barangay__city_municipality', 'purok__barangay')
    search_fields = (
        'user__username',
        'user__first_name',
        'user__last_name',
        'contact_number',
        'street_address',
    )


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'is_active',
        'requires_configuration',
        'is_configured',
        'requires_description',
    )
    list_filter = ('is_active', 'requires_configuration', 'is_configured')
    search_fields = ('name', 'description')
