import calendar
import csv
import logging
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth, TruncWeek
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    AppointmentForm,
    AppointmentReportFilterForm,
    InitialAdminPasswordChangeForm,
    ResidentProfileForm,
    ResidentRegistrationForm,
    ScheduleClosureForm,
    ServiceForm,
)
from .models import (
    Appointment,
    Barangay,
    PurokSitio,
    ResidentProfile,
    ScheduleClosure,
    Service,
    UserSecuritySettings,
)
from .scheduling import (
    ACTIVE_STATUSES,
    SLOT_STARTS,
    day_availability,
    is_business_day,
    slot_availability,
    slot_end,
)

logger = logging.getLogger(__name__)


def _is_staff(user):
    return user.is_authenticated and user.is_staff


@login_required(login_url='login')
def force_password_change(request):
    security_settings = UserSecuritySettings.objects.filter(
        user=request.user,
        must_change_password=True,
    ).first()
    if not security_settings:
        return redirect('home')

    if request.method == 'POST':
        form = InitialAdminPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                security_settings.must_change_password = False
                security_settings.save(update_fields=['must_change_password'])
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been changed.')
            return redirect('home')
    else:
        form = InitialAdminPasswordChangeForm(request.user)

    return render(request, 'Shi_id/force_password_change.html', {'form': form})


def home(request):
    context = {
        'show_admin_dashboard': request.user.is_authenticated and request.user.is_staff,
    }
    if not request.user.is_authenticated:
        return render(request, 'Shi_id/home.html', context)

    if request.user.is_staff:
        appointments = Appointment.objects.select_related('resident', 'service')
        search_term = request.GET.get('q', request.GET.get('appointment_id', '')).strip()
        if search_term:
            appointments = appointments.filter(
                Q(appointment_id__icontains=search_term)
                | Q(resident_name__icontains=search_term)
            )
        selected_service = request.GET.get('service', '').strip()
        if selected_service.isdigit():
            appointments = appointments.filter(service_id=int(selected_service))
        selected_status = request.GET.get('status', '').strip()
        if selected_status in dict(Appointment.STATUS_CHOICES):
            appointments = appointments.filter(status=selected_status)
        selected_date = request.GET.get('date', '').strip()
        if selected_date:
            try:
                appointments = appointments.filter(date=date.fromisoformat(selected_date))
            except ValueError:
                messages.error(request, 'Enter a valid appointment date.')

        all_appointments = Appointment.objects.all()
        today = timezone.localdate()
        context.update({
            'appointments': appointments,
            'appointment_search_query': search_term,
            'services': Service.objects.all(),
            'selected_service': selected_service,
            'selected_status': selected_status,
            'selected_date': selected_date,
            'status_choices': Appointment.STATUS_CHOICES,
            'total_count': all_appointments.count(),
            'pending_count': all_appointments.filter(status='Pending').count(),
            'approved_count': all_appointments.filter(status='Approved').count(),
            'completed_count': all_appointments.filter(status='Completed').count(),
            'cancelled_count': all_appointments.filter(status='Cancelled').count(),
            'resident_count': ResidentProfile.objects.count(),
            'today_count': all_appointments.filter(date=today).count(),
            'upcoming_count': all_appointments.filter(
                date__gt=today,
                status__in=ACTIVE_STATUSES,
            ).count(),
        })
    else:
        appointments = Appointment.objects.filter(
            resident=request.user,
        ).select_related('service')
        search_term = request.GET.get('appointment_id', '').strip()
        if search_term:
            appointments = appointments.filter(appointment_id__iexact=search_term)
        context.update({
            'appointments': appointments,
            'appointment_search_query': search_term,
            'upcoming_appointments': appointments.filter(
                date__gte=timezone.localdate(),
                status__in=ACTIVE_STATUSES,
            ).order_by('date', 'time')[:5],
            'recent_appointments': appointments,
            'profile': ResidentProfile.objects.filter(
                user=request.user,
            ).select_related('purok__barangay__city_municipality').first(),
        })
    return render(request, 'Shi_id/home.html', context)


def _staff_appointments(form, *, include_date_range=True):
    appointments = Appointment.objects.select_related('resident', 'service')
    filters = form.cleaned_data
    if include_date_range:
        if filters['start_date']:
            appointments = appointments.filter(date__gte=filters['start_date'])
        if filters['end_date']:
            appointments = appointments.filter(date__lte=filters['end_date'])
    if filters['service']:
        appointments = appointments.filter(service=filters['service'])
    if filters['status']:
        appointments = appointments.filter(status=filters['status'])
    if filters['resident']:
        appointments = appointments.filter(resident=filters['resident'])
    return appointments


def _report_context(form, appointments):
    filters = form.cleaned_data
    total = appointments.count()
    status_counts = {
        status: appointments.filter(status=status).count()
        for status, _label in Appointment.STATUS_CHOICES
    }
    if filters['period'] == 'day':
        time_rows = appointments.values('date').annotate(
            total=Count('pk'),
        ).order_by('date')
    else:
        truncation = TruncWeek if filters['period'] == 'week' else TruncMonth
        time_rows = appointments.annotate(
            period=truncation('date'),
        ).values('period').annotate(
            total=Count('pk'),
        ).order_by('period')
    time_series = []
    for row in time_rows:
        period = row['date'] if filters['period'] == 'day' else row['period']
        if isinstance(period, datetime):
            period = period.date()
        label = period.strftime('%b %d, %Y' if filters['period'] == 'day' else (
            'Week of %b %d, %Y' if filters['period'] == 'week' else '%b %Y'
        ))
        time_series.append({'label': label, 'count': row['total']})

    service_totals = {}
    for appointment in appointments.iterator():
        label = (
            appointment.service_name_snapshot
            or (appointment.service.name if appointment.service_id else '')
            or appointment.purpose
            or 'Unspecified service'
        )
        service_totals[label] = service_totals.get(label, 0) + 1
    service_series = [
        {'label': label, 'count': count}
        for label, count in sorted(service_totals.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]
    max_time = max((item['count'] for item in time_series), default=1)
    max_service = max((item['count'] for item in service_series), default=1)
    for item in time_series:
        item['width'] = max(4, round(item['count'] / max_time * 100))
    for item in service_series:
        item['width'] = max(4, round(item['count'] / max_service * 100))
    status_series = [
        {'label': status, 'count': status_counts[status]}
        for status, _label in Appointment.STATUS_CHOICES
    ]
    max_status = max((item['count'] for item in status_series), default=1)
    for item in status_series:
        item['width'] = max(4, round(item['count'] / max_status * 100))

    today = timezone.localdate()
    outside_date_range = _staff_appointments(form, include_date_range=False)
    today_count = outside_date_range.filter(date=today).count()
    upcoming_count = outside_date_range.filter(
        date__gt=today,
        status__in=ACTIVE_STATUSES,
    ).count()
    completed_rate = round(status_counts['Completed'] / total * 100, 1) if total else 0
    cancelled_rate = round(status_counts['Cancelled'] / total * 100, 1) if total else 0
    return {
        'total_count': total,
        'status_counts': status_counts,
        'time_series': time_series,
        'service_series': service_series,
        'status_series': status_series,
        'today_count': today_count,
        'upcoming_count': upcoming_count,
        'completed_rate': completed_rate,
        'cancelled_rate': cancelled_rate,
    }


@user_passes_test(_is_staff, login_url='login')
def reports(request):
    params = request.GET.copy()
    if 'period' not in params:
        params['period'] = 'day'
    form = AppointmentReportFilterForm(params)
    context = {'form': form, 'report_ready': form.is_valid()}
    if form.is_valid():
        appointments = _staff_appointments(form).order_by('-date', '-time')
        context.update(_report_context(form, appointments))
        context['appointment_page'] = Paginator(appointments, 25).get_page(request.GET.get('page'))
    return render(request, 'Shi_id/reports.html', context)


@user_passes_test(_is_staff, login_url='login')
def export_appointments_csv(request):
    params = request.GET.copy()
    if 'period' not in params:
        params['period'] = 'day'
    form = AppointmentReportFilterForm(params)
    if not form.is_valid():
        return HttpResponseBadRequest('Invalid report filters; correct them before exporting.')

    appointments = _staff_appointments(form).order_by('-date', '-time')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="barangay-appointments.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Appointment ID', 'Resident', 'Service', 'Request details',
        'Date', 'Start time', 'End time', 'Status', 'Booked at',
    ])
    for appointment in appointments.iterator():
        writer.writerow([
            appointment.appointment_id,
            appointment.resident_name,
            appointment.service_name_snapshot
            or (appointment.service.name if appointment.service_id else ''),
            appointment.purpose,
            appointment.date.isoformat(),
            appointment.time.strftime('%H:%M'),
            appointment.end_time.strftime('%H:%M'),
            appointment.status,
            timezone.localtime(appointment.created_at).isoformat(),
        ])
    return response


@user_passes_test(_is_staff, login_url='login')
def resident_management(request):
    residents = get_user_model().objects.filter(
        resident_profile__isnull=False,
    ).select_related('resident_profile__purok__barangay__city_municipality')
    query = request.GET.get('q', '').strip()
    if query:
        residents = residents.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(resident_profile__contact_number__icontains=query)
        )
    return render(request, 'Shi_id/resident_management.html', {
        'residents': Paginator(residents.order_by('last_name', 'first_name'), 25).get_page(
            request.GET.get('page'),
        ),
        'query': query,
        'can_manage_accounts': any(
            request.user.has_perm(f'auth.{permission}_user')
            for permission in ('view', 'add', 'change')
        ),
    })


@user_passes_test(_is_staff, login_url='login')
def service_management(request):
    service_id = request.POST.get('service_id') if request.method == 'POST' else request.GET.get('edit')
    instance = get_object_or_404(Service, pk=service_id) if service_id else None
    if request.method == 'POST':
        form = ServiceForm(request.POST, instance=instance)
        if form.is_valid():
            service = form.save(commit=False)
            try:
                service.full_clean()
                service.save()
            except (IntegrityError, ValidationError) as error:
                form.add_error(None, f'Service was not saved: {error}')
            else:
                messages.success(request, f'Service “{service.name}” was saved.')
                return redirect('service_management')
    else:
        form = ServiceForm(instance=instance)
    location_admin_links = []
    for model_name, label in (
        ('citymunicipality', 'Cities / Municipalities'),
        ('barangay', 'Barangays'),
        ('puroksitio', 'Puroks / Sitios'),
    ):
        if any(
            request.user.has_perm(f'Shi_id.{permission}_{model_name}')
            for permission in ('view', 'add', 'change')
        ):
            location_admin_links.append({
                'label': label,
                'url': reverse(f'admin:Shi_id_{model_name}_changelist'),
            })
    return render(request, 'Shi_id/service_management.html', {
        'form': form,
        'services': Service.objects.all(),
        'editing_service': instance,
        'location_admin_links': location_admin_links,
        'can_open_admin_site': request.user.is_superuser,
    })


def calendar_context(request):
    today = timezone.localdate()
    month_text = request.GET.get('month')
    try:
        month_start = datetime.strptime(month_text, '%Y-%m').date().replace(day=1) if month_text else today.replace(day=1)
    except ValueError:
        month_start = today.replace(day=1)

    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month)
    first_day = weeks[0][0]
    last_day = weeks[-1][-1]
    closures = list(ScheduleClosure.objects.filter(date__range=(first_day, last_day)))
    appointments = Appointment.objects.filter(
        date__range=(first_day, last_day),
        status__in=ACTIVE_STATUSES,
    ).values_list('date', 'time')
    closures_by_date = {}
    booked_by_date = {}
    for closure in closures:
        closures_by_date.setdefault(closure.date, []).append(closure)
    for booked_date, booked_time in appointments:
        booked_by_date.setdefault(booked_date, set()).add(booked_time)

    month_days = []
    for week in weeks:
        month_days.append([
            {
                'date': day,
                'number': day.day,
                'in_month': day.month == month_start.month,
                'state': day_availability(
                    day,
                    closures=closures_by_date.get(day, []),
                    booked_slots=booked_by_date.get(day, set()),
                ),
            }
            for day in week
        ])

    selected_text = request.POST.get('date') if request.method == 'POST' else request.GET.get('date')
    try:
        selected_day = date.fromisoformat(selected_text) if selected_text else today
    except ValueError:
        selected_day = today
    selected_closures = closures_by_date.get(selected_day)
    if selected_closures is None:
        selected_closures = list(ScheduleClosure.objects.filter(date=selected_day))
    selected_bookings = booked_by_date.get(selected_day)
    if selected_bookings is None:
        selected_bookings = set(
            Appointment.objects.filter(date=selected_day, status__in=ACTIVE_STATUSES)
            .values_list('time', flat=True)
        )

    slots = []
    for start in SLOT_STARTS:
        closure = next((item for item in selected_closures if item.time == start or item.time is None), None)
        slots.append({
            'start': start,
            'end': slot_end(start),
            'state': slot_availability(
                selected_day,
                start,
                closures=selected_closures,
                booked_slots=selected_bookings,
            ),
            'reason': closure.reason if closure else '',
        })

    previous_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        'month_start': month_start,
        'month_label': month_start.strftime('%B %Y'),
        'month_days': month_days,
        'previous_month': previous_month.strftime('%Y-%m'),
        'next_month': next_month.strftime('%Y-%m'),
        'selected_day': selected_day,
        'slots': slots,
        'selected_day_state': day_availability(
            selected_day,
            closures=selected_closures,
            booked_slots=selected_bookings,
        ),
        'selected_appointments': Appointment.objects.filter(date=selected_day).select_related(
            'resident',
            'service',
        ),
        'closures': selected_closures,
        'today': today,
    }


@login_required(login_url='login')
def book_appointment(request):
    profile = ResidentProfile.objects.filter(user=request.user).select_related(
        'purok__barangay__city_municipality',
    ).first()
    if request.method == 'POST':
        form = AppointmentForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    day = form.cleaned_data['date']
                    start = form.cleaned_data['time']
                    if ScheduleClosure.objects.filter(date=day).filter(
                        Q(time__isnull=True) | Q(time=start)
                    ).exists():
                        raise ValueError('This date or time slot has been manually closed by staff.')
                    appointment = form.save(commit=False)
                    appointment.resident = request.user
                    if profile:
                        appointment.resident_address = profile.complete_address
                    appointment.full_clean()
                    appointment.save()
            except IntegrityError:
                form.add_error('time', 'This time slot was just booked. Please choose another available slot.')
            except OperationalError:
                logger.exception('Appointment booking failed while accessing the database.')
                form.add_error(None, 'The booking could not be completed because of a temporary server error. Please try again.')
            except ValidationError as error:
                for field, errors in error.message_dict.items():
                    for error_message in errors:
                        form.add_error(field if field in form.fields else None, error_message)
            except ValueError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, f'Appointment booked successfully. Your Appointment ID is {appointment.appointment_id}.')
                return redirect('appointment_detail', appointment_id=appointment.appointment_id)
    else:
        initial = {'date': request.GET.get('date', timezone.localdate())}
        if profile:
            initial.update({
                'resident_name': profile.full_name,
                'contact_number': profile.contact_number,
            })
        form = AppointmentForm(initial=initial)

    context = calendar_context(request)
    context['form'] = form
    context['service_catalog'] = list(
        form.fields['service'].queryset.values(
            'pk',
            'name',
            'description',
            'requires_description',
        )
    )
    return render(request, 'Shi_id/book_appointment.html', context)


def appointment_availability(request):
    date_text = request.GET.get('date', '')
    try:
        selected_day = date.fromisoformat(date_text)
    except ValueError:
        return JsonResponse({'error': 'Provide a valid date in YYYY-MM-DD format.'}, status=400)

    closures = list(ScheduleClosure.objects.filter(date=selected_day))
    booked_slots = set(
        Appointment.objects.filter(date=selected_day, status__in=ACTIVE_STATUSES)
        .values_list('time', flat=True)
    )
    slots = []
    for start in SLOT_STARTS:
        closure = next((item for item in closures if item.time == start or item.time is None), None)
        slots.append({
            'start': start.strftime('%H:%M'),
            'end': slot_end(start).strftime('%H:%M'),
            'status': slot_availability(
                selected_day,
                start,
                closures=closures,
                booked_slots=booked_slots,
            ),
            'reason': closure.reason if closure else '',
        })
    return JsonResponse({
        'date': selected_day.isoformat(),
        'status': day_availability(selected_day, closures=closures, booked_slots=booked_slots),
        'slots': slots,
    })


def location_barangays(request):
    city_id = request.GET.get('city', '')
    barangays = Barangay.objects.filter(
        city_municipality_id=city_id,
        city_municipality__is_active=True,
        is_active=True,
    ).order_by('name')
    return JsonResponse({
        'locations': [{'id': item.pk, 'name': item.name} for item in barangays],
    })


def location_puroks(request):
    barangay_id = request.GET.get('barangay', '')
    puroks = PurokSitio.objects.filter(
        barangay_id=barangay_id,
        barangay__is_active=True,
        barangay__city_municipality__is_active=True,
        is_active=True,
    ).order_by('pk')
    return JsonResponse({
        'locations': [{'id': item.pk, 'name': item.name} for item in puroks],
    })


@login_required(login_url='login')
def appointment_detail(request, appointment_id):
    appointment_query = Appointment.objects.select_related('resident', 'service')
    if not request.user.is_staff:
        appointment_query = appointment_query.filter(resident=request.user)
    appointment = get_object_or_404(appointment_query, appointment_id=appointment_id)
    return render(request, 'Shi_id/appointment_detail.html', {'appointment': appointment})


@login_required(login_url='login')
@require_POST
def cancel_appointment(request, appointment_id):
    appointment_query = Appointment.objects.select_for_update()
    if not request.user.is_staff:
        appointment_query = appointment_query.filter(resident=request.user)
    with transaction.atomic():
        appointment = get_object_or_404(appointment_query, appointment_id=appointment_id)
        if appointment.status not in ACTIVE_STATUSES:
            messages.error(request, 'Only pending or approved appointments can be cancelled.')
        else:
            appointment.status = 'Cancelled'
            appointment.save(update_fields=['status'])
            messages.success(request, f'Appointment {appointment.appointment_id} was cancelled.')
    return redirect('home')


@user_passes_test(_is_staff, login_url='login')
def schedule_management(request):
    context = calendar_context(request)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'close':
            form = ScheduleClosureForm(request.POST)
            if form.is_valid():
                day = form.cleaned_data['date']
                start = form.cleaned_data['time']
                try:
                    with transaction.atomic():
                        appointments = Appointment.objects.select_for_update().filter(
                            date=day,
                            status__in=ACTIVE_STATUSES,
                        )
                        if start:
                            appointments = appointments.filter(time=start)
                        conflicts = list(appointments)
                        if conflicts:
                            context['conflicting_appointments'] = conflicts
                        else:
                            closure = form.save(commit=False)
                            closure.closed_by = request.user
                            closure.full_clean()
                            closure.save()
                except IntegrityError:
                    form.add_error(None, 'This date or time slot is already closed.')
                except ValidationError as error:
                    form.add_error(None, error)
                except DatabaseError:
                    logger.exception(
                        'Could not save a schedule closure for %s at %s.',
                        day,
                        start or 'all day',
                    )
                    form.add_error(
                        None,
                        'The schedule could not be closed because of a database error. Please try again or contact support.',
                    )
                else:
                    if context.get('conflicting_appointments'):
                        messages.error(
                            request,
                            'Resolve the listed pending or approved appointments before closing this date or slot.',
                        )
                    else:
                        messages.success(request, 'The schedule was closed successfully.')
                        return redirect(f"{request.path}?month={day:%Y-%m}&date={day.isoformat()}")
        elif action == 'reopen':
            closure_id = request.POST.get('closure_id')
            try:
                with transaction.atomic():
                    closure = get_object_or_404(
                        ScheduleClosure.objects.select_for_update(),
                        pk=closure_id,
                    )
                    day = closure.date
                    closure.delete()
            except DatabaseError:
                logger.exception('Could not reopen schedule closure %s.', closure_id)
                messages.error(request, 'The schedule could not be reopened because of a database error.')
            else:
                messages.success(request, 'The schedule was reopened successfully.')
                return redirect(f"{request.path}?month={day:%Y-%m}&date={day.isoformat()}")
        else:
            form = ScheduleClosureForm(initial={'date': context['selected_day']})
            form.add_error(None, 'Choose a valid schedule action.')
    else:
        form = ScheduleClosureForm(initial={'date': context['selected_day']})

    context['form'] = form
    return render(request, 'Shi_id/schedule_management.html', context)


@login_required(login_url='login')
@require_POST
def update_appointment_status(request, appointment_id):
    wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    if not request.user.is_staff:
        if wants_json:
            return JsonResponse(
                {'ok': False, 'message': 'Only authorized staff can update appointment statuses.'},
                status=403,
            )
        messages.error(request, 'Only authorized staff can update appointment statuses.')
        return redirect('home')

    try:
        appointment = get_object_or_404(Appointment, appointment_id=appointment_id)
    except Http404:
        if wants_json:
            return JsonResponse(
                {'ok': False, 'message': 'The requested appointment could not be found.'},
                status=404,
            )
        raise
    new_status = request.POST.get('status')
    try:
        with transaction.atomic():
            appointment.status = new_status
            appointment.full_clean()
            appointment.save(update_fields=['status'])
    except (ValueError, IntegrityError, ValidationError) as error:
        details = '; '.join(error.messages) if isinstance(error, ValidationError) else str(error)
        error_message = f'Appointment status was not changed: {details}'
        if wants_json:
            return JsonResponse({'ok': False, 'message': error_message}, status=400)
        messages.error(request, error_message)
    except DatabaseError:
        logger.exception('Database error while updating appointment %s.', appointment_id)
        error_message = 'Appointment status was not changed because of a database error.'
        if wants_json:
            return JsonResponse({'ok': False, 'message': error_message}, status=500)
        messages.error(request, error_message)
    else:
        success_message = f'Appointment {appointment.appointment_id} is now {appointment.status}.'
        if wants_json:
            return JsonResponse({
                'ok': True,
                'appointment_id': appointment.appointment_id,
                'status': appointment.status,
                'message': success_message,
            })
        messages.success(request, success_message)

    return_to = request.POST.get('return_to', '')
    if not url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}):
        return_to = reverse('home')
    return redirect(return_to)


def signup(request):
    if request.method == 'POST':
        form = ResidentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('home')
    else:
        form = ResidentRegistrationForm()

    return render(request, 'Shi_id/signup.html', {'form': form})


@login_required(login_url='login')
def resident_profile(request):
    profile = ResidentProfile.objects.filter(
        user=request.user,
    ).select_related(
        'purok__barangay__city_municipality',
    ).first()
    if profile and request.method == 'POST':
        form = ResidentProfileForm(request.POST, instance=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile was updated.')
            return redirect('resident_profile')
    elif profile:
        form = ResidentProfileForm(instance=profile, user=request.user)
    else:
        form = None
    return render(request, 'Shi_id/resident_profile.html', {
        'profile': profile,
        'form': form,
        'staff_profile': request.user.is_staff,
    })
