import os
from io import StringIO
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ResidentRegistrationForm, normalize_person_name
from .models import (
    Appointment,
    Barangay,
    CityMunicipality,
    PurokSitio,
    ResidentProfile,
    ScheduleClosure,
    Service,
    UserSecuritySettings,
)
from .scheduling import SLOT_STARTS


def next_weekday(start=None):
    day = start or timezone.localdate()
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class LoginFlowTests(TestCase):
    def test_login_page_loads(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_user_can_log_in(self):
        user = get_user_model().objects.create_user(username='resident', password='secret123')
        response = self.client.post(
            reverse('login'),
            {'username': user.username, 'password': 'secret123'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)


class HomeDashboardTests(TestCase):
    def setUp(self):
        self.appointment = Appointment.objects.create(
            appointment_id='APP-001',
            resident_name='Private Resident',
            contact_number='09123456789',
            date=date(2026, 10, 10),
            time=time(9, 30),
            purpose='Certificate request',
        )

    def test_public_home_hides_appointment_details_and_dashboard(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Log In')
        self.assertContains(response, 'Register')
        self.assertNotContains(response, 'Logout')
        self.assertNotContains(response, 'Book an Appointment')
        self.assertNotContains(response, self.appointment.appointment_id)
        self.assertNotContains(response, self.appointment.resident_name)
        self.assertNotContains(response, self.appointment.contact_number)
        self.assertNotContains(response, 'Total Appointments')

    def test_resident_only_sees_their_own_appointments(self):
        User = get_user_model()
        own_user = User.objects.create_user(username='resident', password='secret123')
        other_user = User.objects.create_user(username='other', password='secret123')
        own = Appointment.objects.create(
            appointment_id='APP-OWN', resident=own_user, resident_name='Resident',
            contact_number='09123456789', date=next_weekday(), time=time(10),
            purpose='Own appointment',
        )
        Appointment.objects.create(
            appointment_id='APP-OTHER', resident=other_user, resident_name='Other',
            contact_number='09999999999', date=next_weekday(), time=time(10, 30),
            purpose='Private appointment',
        )
        self.client.force_login(own_user)
        response = self.client.get(reverse('home'))
        self.assertContains(response, own.appointment_id)
        self.assertNotContains(response, 'APP-OTHER')
        self.assertContains(response, reverse('resident_profile'))
        self.assertNotContains(response, reverse('schedule_management'))
        self.assertNotContains(response, 'Logout')

    def test_logout_is_available_from_profile_and_returns_to_public_landing(self):
        user = get_user_model().objects.create_user(username='logout-resident', password='secret123')
        self.client.force_login(user)
        home_response = self.client.get(reverse('home'))
        self.assertNotContains(home_response, reverse('logout'))
        profile_response = self.client.get(reverse('resident_profile'))
        self.assertContains(profile_response, reverse('logout'))
        response = self.client.post(reverse('logout'), follow=True)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, 'Log In')
        self.assertContains(response, 'Register')
        self.assertNotContains(response, 'Book an Appointment')

    def test_staff_home_shows_appointment_dashboard(self):
        user = get_user_model().objects.create_user(
            username='staff', password='secret123', is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertContains(response, self.appointment.appointment_id)
        self.assertContains(response, 'Total Appointments')
        self.assertContains(response, reverse('schedule_management'))
        self.assertContains(response, reverse('resident_management'))
        self.assertContains(response, reverse('service_management'))
        self.assertContains(response, reverse('reports'))
        self.assertContains(response, reverse('home') + '#appointments')
        self.assertNotContains(response, reverse('logout'))

    def test_staff_profile_contains_admin_navigation_and_logout_but_home_does_not(self):
        staff = get_user_model().objects.create_superuser(
            username='profile-admin',
            email='admin@example.com',
            password='secret123',
        )
        self.client.force_login(staff)
        home = self.client.get(reverse('home'))
        self.assertNotContains(home, reverse('logout'))
        profile = self.client.get(reverse('resident_profile'))
        self.assertContains(profile, reverse('logout'))
        for url_name in (
            'schedule_management',
            'resident_management',
            'service_management',
            'reports',
        ):
            self.assertContains(profile, reverse(url_name))
        settings = self.client.get(reverse('service_management'))
        self.assertContains(settings, reverse('admin:Shi_id_puroksitio_changelist'))


class AppointmentStatusUpdateTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='status-staff',
            password='secret123',
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.appointment = Appointment.objects.create(
            appointment_id='BRGY-2026-0099',
            resident_name='Status Resident',
            contact_number='09123456789',
            date=date(2026, 10, 19),
            time=time(10),
            purpose='Status update test',
        )
        self.url = reverse(
            'update_appointment_status',
            args=[self.appointment.appointment_id],
        )

    def test_ajax_approval_returns_updated_status_without_redirect(self):
        response = self.client.post(
            self.url,
            {'status': 'Approved'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Approved')
        self.assertEqual(response.json()['appointment_id'], self.appointment.appointment_id)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, 'Approved')

    def test_ajax_cancellation_returns_updated_status_without_redirect(self):
        self.appointment.status = 'Approved'
        self.appointment.save(update_fields=['status'])
        response = self.client.post(
            self.url,
            {'status': 'Cancelled'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Cancelled')
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, 'Cancelled')

    def test_ajax_failure_returns_validation_message_and_preserves_status(self):
        self.appointment.status = 'Completed'
        self.appointment.save(update_fields=['status'])
        response = self.client.post(
            self.url,
            {'status': 'Cancelled'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('status transition is not allowed', response.json()['message'])
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, 'Completed')

    def test_non_ajax_fallback_returns_to_filtered_appointment_row(self):
        return_to = '/?q=Status+Resident&status=Pending&date=2026-10-19#appointment-BRGY-2026-0099'
        response = self.client.post(
            self.url,
            {'status': 'Approved', 'return_to': return_to},
        )

        self.assertRedirects(response, return_to, fetch_redirect_response=False)

    def test_external_fallback_redirect_is_rejected(self):
        response = self.client.post(
            self.url,
            {'status': 'Approved', 'return_to': 'https://example.com/'},
        )

        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class BookingFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='resident', password='secret123')
        self.service = Service.objects.get(name='Barangay Clearance')
        self.client.force_login(self.user)
        self.day = next_weekday()

    def booking_data(self, **overrides):
        data = {
            'resident_name': 'New Resident',
            'contact_number': '09123456789',
            'date': self.day.isoformat(),
            'time': '10:00',
            'service': str(self.service.pk),
            'purpose': 'Certificate request',
        }
        data.update(overrides)
        return data

    def test_booking_generates_an_id_and_scoped_confirmation(self):
        response = self.client.post(reverse('book_appointment'), self.booking_data())
        appointment = Appointment.objects.get(resident=self.user)
        self.assertEqual(appointment.service, self.service)
        self.assertEqual(appointment.service_name_snapshot, self.service.name)
        self.assertRedirects(
            response,
            reverse('appointment_detail', args=[appointment.appointment_id]),
            fetch_redirect_response=False,
        )
        self.assertRegex(appointment.appointment_id, rf'^BRGY-{self.day.year}-\d{{4,}}$')
        confirmation = self.client.get(response.url)
        self.assertContains(confirmation, appointment.appointment_id)
        self.assertContains(confirmation, self.service.name)

    def test_other_service_requires_resident_details(self):
        other_service = Service.objects.get(name='Other Barangay Services')
        response = self.client.post(
            reverse('book_appointment'),
            self.booking_data(service=str(other_service.pk), purpose=''),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Appointment.objects.filter(resident=self.user).exists())
        self.assertContains(response, 'Describe the service you need.')

    def test_unconfigured_medical_services_are_not_bookable(self):
        response = self.client.get(reverse('book_appointment'))
        self.assertNotContains(response, 'Medical Consultation')
        self.assertNotContains(response, 'Health Center Check-up')

    def test_booking_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('book_appointment'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('book_appointment')}")

    def test_weekends_and_non_schedule_times_are_rejected(self):
        saturday = self.day
        while saturday.weekday() != 5:
            saturday += timedelta(days=1)
        weekend = self.client.post(
            reverse('book_appointment'),
            self.booking_data(date=saturday.isoformat()),
        )
        self.assertEqual(weekend.status_code, 200)
        self.assertFalse(Appointment.objects.filter(resident=self.user).exists())

        off_schedule = self.client.post(
            reverse('book_appointment'),
            self.booking_data(time='09:30'),
        )
        self.assertEqual(off_schedule.status_code, 200)
        self.assertFalse(Appointment.objects.filter(resident=self.user).exists())

    def test_database_prevents_two_active_reservations_for_one_slot(self):
        Appointment.objects.create(
            appointment_id='APP-FIRST',
            resident=self.user,
            resident_name='First',
            contact_number='09123456789',
            date=self.day,
            time=SLOT_STARTS[0],
            purpose='First',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Appointment.objects.create(
                    appointment_id='APP-SECOND',
                    resident_name='Second',
                    contact_number='09999999999',
                    date=self.day,
                    time=SLOT_STARTS[0],
                    purpose='Second',
                )

    def test_closed_schedule_is_not_bookable(self):
        staff = get_user_model().objects.create_user(username='staff', password='secret123', is_staff=True)
        ScheduleClosure.objects.create(date=self.day, reason='Office event', closed_by=staff)
        response = self.client.post(reverse('book_appointment'), self.booking_data())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Appointment.objects.filter(resident=self.user).exists())

    def test_availability_endpoint_generates_all_sessions_and_marks_unavailable_slots(self):
        Appointment.objects.create(
            resident_name='Booked resident',
            contact_number='09123456789',
            date=self.day,
            time=time(10),
            purpose='Certificate',
        )
        ScheduleClosure.objects.create(date=self.day, time=time(10, 30), reason='Staff meeting')
        response = self.client.get(
            reverse('appointment_availability'),
            {'date': self.day.isoformat()},
        )
        data = response.json()
        self.assertEqual(len(data['slots']), 10)
        self.assertEqual(
            data['slots'][0],
            {'start': '10:00', 'end': '10:30', 'status': 'red', 'reason': ''},
        )
        self.assertEqual(
            data['slots'][1],
            {'start': '10:30', 'end': '11:00', 'status': 'red', 'reason': 'Staff meeting'},
        )
        self.assertEqual(
            data['slots'][-1],
            {'start': '14:30', 'end': '15:00', 'status': 'green', 'reason': ''},
        )

    def test_cancelling_a_reservation_releases_the_slot(self):
        appointment = Appointment.objects.create(
            resident=self.user,
            resident_name='New Resident',
            contact_number='09123456789',
            date=self.day,
            time=time(10),
            purpose='Certificate',
        )
        response = self.client.post(
            reverse('cancel_appointment', args=[appointment.appointment_id]),
            follow=True,
        )
        self.assertContains(response, 'was cancelled')
        new_appointment = Appointment.objects.create(
            resident_name='Another Resident',
            contact_number='09999999999',
            date=self.day,
            time=time(10),
            purpose='Certificate',
        )
        self.assertEqual(new_appointment.status, 'Pending')

    def test_resident_cannot_access_another_residents_appointment(self):
        other = get_user_model().objects.create_user(username='other', password='secret123')
        appointment = Appointment.objects.create(
            appointment_id='BRGY-2026-9999',
            resident=other,
            resident_name='Other',
            contact_number='09999999999',
            date=self.day,
            time=time(10),
            purpose='Private',
        )
        response = self.client.get(reverse('appointment_detail', args=[appointment.appointment_id]))
        self.assertEqual(response.status_code, 404)


class ResidentRegistrationTests(TestCase):
    def setUp(self):
        self.city = CityMunicipality.objects.get(psgc_code='072251000')
        self.barangay = Barangay.objects.get(psgc_code='072251001')
        self.purok = PurokSitio.objects.create(barangay=self.barangay, name='Test Purok')

    def registration_data(self, **overrides):
        data = {
            'username': 'czelle',
            'first_name': '  czelle   adrienne ',
            'middle_name': "o'CONNOR",
            'last_name': 'gesite-smith',
            'email': 'Resident.Example@Example.com ',
            'contact_number': ' 09123456789 ',
            'city_municipality': str(self.city.pk),
            'barangay': str(self.barangay.pk),
            'purok': str(self.purok.pk),
            'street_address': '  12  Main   Street ',
            'password1': 'abcdef',
            'password2': 'abcdef',
        }
        data.update(overrides)
        return data

    def test_server_normalizes_names_and_saves_profile_and_location(self):
        self.assertEqual(normalize_person_name("mary-jane o'connor"), "Mary-Jane O'Connor")
        response = self.client.post(reverse('signup'), self.registration_data())
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        user = get_user_model().objects.get(username='czelle')
        profile = ResidentProfile.objects.get(user=user)
        self.assertEqual(user.first_name, 'Czelle Adrienne')
        self.assertEqual(profile.middle_name, "O'Connor")
        self.assertEqual(user.last_name, 'Gesite-Smith')
        self.assertEqual(user.email, 'Resident.Example@Example.com')
        self.assertEqual(profile.contact_number, '09123456789')
        self.assertEqual(profile.complete_address, '12 Main Street, Test Purok, Awihao, City of Toledo, Cebu')
        self.assertTrue(user.check_password('abcdef'))
        self.assertNotEqual(user.password, 'abcdef')

    def test_registration_rejects_password_shorter_than_six_characters(self):
        response = self.client.post(
            reverse('signup'),
            self.registration_data(password1='abcde', password2='abcde'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='czelle').exists())
        self.assertIn(
            'This password is too short. It must contain at least 6 characters.',
            response.context['form'].errors['password2'],
        )

    def test_registration_requires_matching_password_confirmation(self):
        response = self.client.post(
            reverse('signup'),
            self.registration_data(password1='abcdef', password2='ghijkl'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='czelle').exists())
        self.assertEqual(
            list(response.context['form'].errors['password2']),
            ['The two password fields didn’t match.'],
        )

    def test_registration_rejects_mismatched_location_hierarchy(self):
        another_city = CityMunicipality.objects.create(
            name='Other City', province='Cebu', psgc_code='TEST-CITY-001',
        )
        response = self.client.post(
            reverse('signup'),
            self.registration_data(city_municipality=str(another_city.pk)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='czelle').exists())
        self.assertContains(response, 'Choose a barangay belonging to the selected city or municipality.')

    def test_location_endpoints_only_return_active_children(self):
        inactive = Barangay.objects.create(
            city_municipality=self.city,
            name='Inactive test location',
            psgc_code='TEST-BRG-001',
            is_active=False,
        )
        PurokSitio.objects.create(barangay=inactive, name='Hidden Purok')
        barangays_response = self.client.get(
            reverse('location_barangays'),
            {'city': self.city.pk},
        )
        barangay_ids = [item['id'] for item in barangays_response.json()['locations']]
        self.assertIn(self.barangay.pk, barangay_ids)
        self.assertNotIn(inactive.pk, barangay_ids)
        self.purok.is_active = False
        self.purok.save(update_fields=['is_active'])
        PurokSitio.objects.filter(barangay=self.barangay).update(is_active=False)
        puroks_response = self.client.get(
            reverse('location_puroks'),
            {'barangay': self.barangay.pk},
        )
        self.assertEqual(puroks_response.json()['locations'], [])

    def test_default_puroks_exist_in_all_toledo_barangays_and_are_ordered(self):
        barangay_count = Barangay.objects.filter(
            city_municipality=self.city,
            is_active=True,
        ).count()
        self.assertEqual(barangay_count, 38)
        self.assertEqual(
            PurokSitio.objects.filter(
                barangay__city_municipality=self.city,
                name__startswith='Purok ',
            ).count(),
            barangay_count * 15,
        )

        response = self.client.get(
            reverse('location_puroks'),
            {'barangay': self.barangay.pk},
        )
        self.assertEqual(
            [
                location['name']
                for location in response.json()['locations']
                if location['name'].startswith('Purok ')
            ],
            [f'Purok {number}' for number in range(1, 16)],
        )

    def test_purok_names_are_unique_case_insensitively_per_barangay(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PurokSitio.objects.create(
                    barangay=self.barangay,
                    name='pUrOk 1',
                )

    def test_inactive_saved_purok_remains_attached_to_resident_profile(self):
        user = get_user_model().objects.create_user(
            username='resident-with-inactive-purok',
            password='secret123',
            first_name='Resident',
            last_name='Test',
        )
        profile = ResidentProfile.objects.create(
            user=user,
            middle_name='',
            contact_number='09123456789',
            purok=self.purok,
            street_address='12 Main Street',
        )
        saved_address = profile.complete_address
        profile.purok.is_active = False
        profile.purok.save(update_fields=['is_active'])

        self.client.force_login(user)
        response = self.client.get(reverse('resident_profile'))
        self.assertContains(response, saved_address)
        profile.refresh_from_db()
        self.assertEqual(profile.purok_id, self.purok.pk)

    def test_profile_and_appointment_display_saved_address(self):
        response = self.client.post(reverse('signup'), self.registration_data())
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='czelle')
        self.client.force_login(user)
        profile_response = self.client.get(reverse('resident_profile'))
        self.assertContains(profile_response, '12 Main Street, Test Purok, Awihao, City of Toledo, Cebu')

        day = next_weekday()
        booking_response = self.client.post(
            reverse('book_appointment'),
            {
                'resident_name': 'czelle adrienne gesite',
                'contact_number': '09123456789',
                'date': day.isoformat(),
                'time': '10:00',
                'service': str(Service.objects.get(name='Barangay Clearance').pk),
                'purpose': 'Certificate request',
            },
        )
        appointment = Appointment.objects.get(resident=user)
        self.assertEqual(
            appointment.resident_address,
            '12 Main Street, Test Purok, Awihao, City of Toledo, Cebu',
        )
        self.assertEqual(booking_response.status_code, 302)

    def test_resident_can_update_permitted_profile_information(self):
        self.client.post(reverse('signup'), self.registration_data())
        user = get_user_model().objects.get(username='czelle')
        profile = ResidentProfile.objects.get(user=user)
        self.client.force_login(user)
        response = self.client.post(
            reverse('resident_profile'),
            {
                'first_name': 'updated resident',
                'middle_name': profile.middle_name,
                'last_name': user.last_name,
                'email': user.email,
                'contact_number': '09987654321',
                'city_municipality': str(self.city.pk),
                'barangay': str(self.barangay.pk),
                'purok': str(self.purok.pk),
                'street_address': '99 New Street',
            },
        )
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertRedirects(response, reverse('resident_profile'), fetch_redirect_response=False)
        self.assertEqual(user.first_name, 'Updated Resident')
        self.assertEqual(user.username, 'czelle')
        self.assertEqual(profile.contact_number, '09987654321')
        self.assertEqual(profile.street_address, '99 New Street')


class StaffReportsAndServicesTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='report-staff',
            password='secret123',
            is_staff=True,
        )
        self.resident = get_user_model().objects.create_user(
            username='report-resident',
            password='secret123',
            first_name='Report',
            last_name='Resident',
        )
        self.service = Service.objects.get(name='Barangay Clearance')
        self.day = next_weekday()
        self.first = Appointment.objects.create(
            resident=self.resident,
            service=self.service,
            service_name_snapshot=self.service.name,
            resident_name='Report Resident',
            contact_number='09123456789',
            resident_address='Private address',
            date=self.day,
            time=time(10),
            purpose='Clearance request',
        )
        Appointment.objects.create(
            resident=self.resident,
            service=self.service,
            service_name_snapshot=self.service.name,
            resident_name='Report Resident',
            contact_number='09123456789',
            date=self.day,
            time=time(10, 30),
            purpose='Completed request',
            status='Completed',
        )
        self.client.force_login(self.staff)

    def test_reports_apply_filters_and_show_actual_summaries(self):
        response = self.client.get(
            reverse('reports'),
            {
                'start_date': self.day.isoformat(),
                'end_date': self.day.isoformat(),
                'service': str(self.service.pk),
                'status': 'Pending',
                'period': 'day',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_count'], 1)
        self.assertEqual(response.context['status_counts']['Pending'], 1)
        self.assertContains(response, self.first.appointment_id)
        self.assertNotContains(response, 'Private address')
        for period in ('week', 'month'):
            grouped_response = self.client.get(
                reverse('reports'),
                {'service': str(self.service.pk), 'period': period},
            )
            self.assertEqual(grouped_response.status_code, 200)
            self.assertEqual(grouped_response.context['total_count'], 2)
            self.assertEqual(len(grouped_response.context['time_series']), 1)

    def test_csv_export_uses_filters_and_excludes_contact_and_address(self):
        response = self.client.get(
            reverse('export_appointments_csv'),
            {
                'start_date': self.day.isoformat(),
                'end_date': self.day.isoformat(),
                'service': str(self.service.pk),
                'status': 'Pending',
                'period': 'day',
            },
        )
        content = response.content.decode()
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn(self.first.appointment_id, content)
        self.assertNotIn('09123456789', content)
        self.assertNotIn('Private address', content)
        self.assertNotIn('completed request', content.lower())

        invalid = self.client.get(
            reverse('export_appointments_csv'),
            {'start_date': 'not-a-date', 'period': 'day'},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_service_management_is_staff_only_and_can_create_service(self):
        response = self.client.post(
            reverse('service_management'),
            {
                'name': 'Local document review',
                'description': 'Review required documents at the barangay office.',
                'is_active': 'on',
            },
        )
        self.assertRedirects(response, reverse('service_management'), fetch_redirect_response=False)
        self.assertTrue(Service.objects.filter(name='Local document review', is_active=True).exists())

        resident_client = self.client_class()
        resident_client.force_login(self.resident)
        denied = resident_client.get(reverse('service_management'))
        self.assertEqual(denied.status_code, 302)
        self.assertIn(reverse('login'), denied.url)

    def test_reports_are_not_available_to_residents(self):
        resident_client = self.client_class()
        resident_client.force_login(self.resident)
        response = resident_client.get(reverse('reports'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class AccountSetupAndResetTests(TestCase):
    def setUp(self):
        self.city = CityMunicipality.objects.get(psgc_code='072251000')
        self.barangay = Barangay.objects.get(psgc_code='072251001')
        self.purok = PurokSitio.objects.get(barangay=self.barangay, name='Purok 1')
        self.service = Service.objects.get(name='Barangay Clearance')

    def test_default_admin_is_created_with_hashed_password_and_forced_change(self):
        password = 'Temporary-Admin-Pass'
        with patch.dict(os.environ, {'DEFAULT_ADMIN_PASSWORD': password}):
            call_command('create_default_admin', stdout=StringIO())

        admin = get_user_model().objects.get(username='admin')
        self.assertTrue(admin.is_active)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.has_usable_password())
        self.assertTrue(admin.check_password(password))
        self.assertNotEqual(admin.password, password)
        self.assertTrue(admin.security_settings.must_change_password)

        response = self.client.post(
            reverse('login'),
            {'username': 'admin', 'password': password},
            follow=True,
        )
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertTrue(any(
            url.endswith(reverse('force_password_change'))
            for url, _status in response.redirect_chain
        ))
        self.assertEqual(
            self.client.get(reverse('admin:index')).status_code,
            302,
        )

        same_password_response = self.client.post(
            reverse('force_password_change'),
            {
                'old_password': password,
                'new_password1': password,
                'new_password2': password,
            },
        )
        self.assertEqual(same_password_response.status_code, 200)
        self.assertContains(
            same_password_response,
            'Choose a new password different from the initial password.',
        )
        self.assertTrue(admin.security_settings.must_change_password)

        changed_password = 'New-Admin-Pass-2026'
        response = self.client.post(
            reverse('force_password_change'),
            {
                'old_password': password,
                'new_password1': changed_password,
                'new_password2': changed_password,
            },
            follow=True,
        )
        admin.refresh_from_db()
        admin.security_settings.refresh_from_db()
        self.assertTrue(admin.check_password(changed_password))
        self.assertFalse(admin.security_settings.must_change_password)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_setup_is_idempotent_and_does_not_change_existing_admin_credentials(self):
        User = get_user_model()
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='Original-Admin-Password',
        )
        with patch.dict(os.environ, {'DEFAULT_ADMIN_PASSWORD': 'Different-Admin-Password'}):
            call_command('create_default_admin', stdout=StringIO())
        admin.refresh_from_db()
        self.assertTrue(admin.check_password('Original-Admin-Password'))
        self.assertFalse(UserSecuritySettings.objects.filter(user=admin).exists())
        self.assertEqual(User.objects.filter(username='admin').count(), 1)

    def test_explicit_admin_password_reset_backs_up_and_requires_password_change(self):
        admin = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='Original-Admin-Password',
        )
        backup = 'verified-test-backup.sqlite3'
        with (
            patch.dict(os.environ, {'DEFAULT_ADMIN_PASSWORD': 'admin@123'}),
            patch(
                'Shi_id.management.commands.create_default_admin._create_sqlite_backup',
                return_value=backup,
            ) as create_backup,
        ):
            call_command('create_default_admin', '--reset-existing', '--noinput', stdout=StringIO())

        admin.refresh_from_db()
        self.assertTrue(admin.check_password('admin@123'))
        self.assertFalse(admin.check_password('Original-Admin-Password'))
        self.assertTrue(admin.security_settings.must_change_password)
        self.assertEqual(get_user_model().objects.filter(username='admin').count(), 1)
        create_backup.assert_called_once_with()

    def test_resident_deletion_preserves_admin_appointments_and_frees_gmail(self):
        User = get_user_model()
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='Original-Admin-Password',
        )
        staff = User.objects.create_user(
            username='staff',
            email='staff@example.com',
            password='Original-Staff-Password',
            is_staff=True,
        )
        resident = User.objects.create_user(
            username='previous-resident',
            email='previously.used.resident@gmail.com',
            password='Resident-Password',
            first_name='Old',
            last_name='Resident',
        )
        ResidentProfile.objects.create(
            user=resident,
            middle_name='',
            contact_number='09123456789',
            purok=self.purok,
            street_address='Old address',
        )
        appointment_date = next_weekday()
        old_appointment = Appointment.objects.create(
            resident=resident,
            resident_name='Old Resident',
            contact_number='09123456789',
            date=appointment_date,
            time=time(10),
            purpose='Existing appointment',
        )
        saved_admin_password = admin.password

        call_command('delete_resident_accounts', '--noinput', stdout=StringIO())

        admin.refresh_from_db()
        staff.refresh_from_db()
        old_appointment.refresh_from_db()
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.password, saved_admin_password)
        self.assertTrue(staff.is_staff)
        self.assertTrue(staff.check_password('Original-Staff-Password'))
        self.assertFalse(User.objects.filter(username='previous-resident').exists())
        self.assertFalse(User.objects.filter(email__iexact='previously.used.resident@gmail.com').exists())
        self.assertFalse(ResidentProfile.objects.filter(user=resident).exists())
        self.assertIsNone(old_appointment.resident_id)
        self.assertTrue(Appointment.objects.filter(pk=old_appointment.pk).exists())

        response = self.client.post(
            reverse('signup'),
            {
                'username': 'new-resident',
                'first_name': 'New',
                'middle_name': '',
                'last_name': 'Resident',
                'email': 'previously.used.resident@gmail.com',
                'contact_number': '09987654321',
                'city_municipality': str(self.city.pk),
                'barangay': str(self.barangay.pk),
                'purok': str(self.purok.pk),
                'street_address': 'New address',
                'password1': 'abcdef',
                'password2': 'abcdef',
            },
        )
        self.assertEqual(response.status_code, 302)
        new_resident = User.objects.get(username='new-resident')
        self.assertEqual(new_resident.email, 'previously.used.resident@gmail.com')
        self.assertTrue(new_resident.check_password('abcdef'))

        self.client.logout()
        login_response = self.client.post(
            reverse('login'),
            {'username': 'new-resident', 'password': 'abcdef'},
            follow=True,
        )
        self.assertTrue(login_response.wsgi_request.user.is_authenticated)
        self.assertEqual(login_response.wsgi_request.user.pk, new_resident.pk)
        booking_response = self.client.post(
            reverse('book_appointment'),
            {
                'resident_name': 'New Resident',
                'contact_number': '09987654321',
                'date': appointment_date.isoformat(),
                'time': '10:30',
                'service': str(self.service.pk),
                'purpose': 'New booking',
            },
        )
        self.assertEqual(booking_response.status_code, 302)
        self.assertEqual(Appointment.objects.filter(resident=new_resident).count(), 1)


class StaffScheduleTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username='staff', password='secret123', is_staff=True)
        self.resident = get_user_model().objects.create_user(username='resident', password='secret123')
        self.day = next_weekday()

    def test_residents_cannot_manage_schedule(self):
        self.client.force_login(self.resident)
        response = self.client.get(reverse('schedule_management'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_closure_requires_staff_and_reason_and_can_be_reopened(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('schedule_management'),
            {'action': 'close', 'date': self.day.isoformat(), 'time': '', 'reason': 'Office event'},
        )
        closure = ScheduleClosure.objects.get(date=self.day, time__isnull=True)
        self.assertRedirects(response, f"{reverse('schedule_management')}?month={self.day:%Y-%m}&date={self.day.isoformat()}", fetch_redirect_response=False)

        self.client.post(
            reverse('schedule_management'),
            {'action': 'reopen', 'closure_id': closure.pk},
        )
        self.assertFalse(ScheduleClosure.objects.filter(pk=closure.pk).exists())

    def test_close_day_blocks_resident_booking_and_reopen_restores_otherwise_open_slots(self):
        self.client.force_login(self.staff)
        slot_closure = ScheduleClosure.objects.create(
            date=self.day,
            time=time(10, 30),
            reason='Staff training',
            closed_by=self.staff,
        )
        response = self.client.post(
            reverse('schedule_management'),
            {
                'action': 'close',
                'date': self.day.isoformat(),
                'time': '',
                'reason': 'Office event',
            },
        )
        self.assertRedirects(
            response,
            f"{reverse('schedule_management')}?month={self.day:%Y-%m}&date={self.day.isoformat()}",
            fetch_redirect_response=False,
        )
        day_closure = ScheduleClosure.objects.get(date=self.day, time__isnull=True)

        availability = self.client.get(
            reverse('appointment_availability'),
            {'date': self.day.isoformat()},
        ).json()
        self.assertEqual(availability['status'], 'red')
        self.assertTrue(all(slot['status'] == 'red' for slot in availability['slots']))

        self.client.force_login(self.resident)
        booking = self.client.post(
            reverse('book_appointment'),
            {
                'resident_name': 'Resident',
                'contact_number': '09123456789',
                'date': self.day.isoformat(),
                'time': '10:00',
                'service': str(Service.objects.get(name='Barangay Clearance').pk),
                'purpose': 'Certificate',
            },
        )
        self.assertEqual(booking.status_code, 200)
        self.assertFalse(Appointment.objects.filter(resident=self.resident, date=self.day).exists())
        self.assertContains(booking, 'This date has been manually closed by staff.')

        self.client.force_login(self.staff)
        reopened = self.client.post(
            reverse('schedule_management'),
            {'action': 'reopen', 'closure_id': day_closure.pk},
        )
        self.assertRedirects(
            reopened,
            f"{reverse('schedule_management')}?month={self.day:%Y-%m}&date={self.day.isoformat()}",
            fetch_redirect_response=False,
        )
        self.assertFalse(ScheduleClosure.objects.filter(pk=day_closure.pk).exists())
        self.assertTrue(ScheduleClosure.objects.filter(pk=slot_closure.pk).exists())

        availability = self.client.get(
            reverse('appointment_availability'),
            {'date': self.day.isoformat()},
        ).json()
        self.assertEqual(availability['status'], 'green')
        self.assertEqual(availability['slots'][0]['status'], 'green')
        self.assertEqual(availability['slots'][1]['status'], 'red')

    def test_close_day_warns_for_active_appointments_and_does_not_save(self):
        Appointment.objects.create(
            resident=self.resident,
            resident_name='Resident',
            contact_number='09123456789',
            date=self.day,
            time=time(10),
            purpose='Certificate',
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('schedule_management'),
            {
                'action': 'close',
                'date': self.day.isoformat(),
                'time': '',
                'reason': 'Office event',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resolve the listed pending or approved appointments')
        self.assertContains(response, 'BRGY-')
        self.assertFalse(ScheduleClosure.objects.filter(date=self.day, time__isnull=True).exists())

    def test_invalid_close_day_request_shows_validation_error_without_saving(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('schedule_management'),
            {'action': 'close', 'date': '', 'time': '', 'reason': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required.')
        self.assertFalse(ScheduleClosure.objects.exists())

    def test_resident_cannot_reopen_schedule_closure(self):
        closure = ScheduleClosure.objects.create(
            date=self.day,
            reason='Office event',
            closed_by=self.staff,
        )
        self.client.force_login(self.resident)
        response = self.client.post(
            reverse('schedule_management'),
            {'action': 'reopen', 'closure_id': closure.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ScheduleClosure.objects.filter(pk=closure.pk).exists())

    def test_closure_is_blocked_until_active_appointments_are_resolved(self):
        Appointment.objects.create(
            appointment_id='APP-BOOKED', resident=self.resident,
            resident_name='Resident', contact_number='09123456789',
            date=self.day, time=time(10), purpose='Certificate',
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('schedule_management'),
            {'action': 'close', 'date': self.day.isoformat(), 'time': '10:00', 'reason': 'Maintenance'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resolve the listed pending or approved appointments')
        self.assertFalse(ScheduleClosure.objects.filter(date=self.day).exists())


class AppointmentAdminTests(TestCase):
    def test_staff_can_approve_pending_appointment_in_admin(self):
        user = get_user_model().objects.create_superuser(
            username='admin', password='secret123', email='admin@example.com',
        )
        self.client.force_login(user)
        appointment = Appointment.objects.create(
            appointment_id='APP-002', resident_name='Resident',
            contact_number='09123456789', date=date(2026, 10, 10),
            time=time(9, 30), purpose='Certificate request',
        )
        response = self.client.post(
            reverse('admin:Shi_id_appointment_change', args=[appointment.pk]),
            {
                'appointment_id': appointment.appointment_id,
                'resident_name': appointment.resident_name,
                'contact_number': appointment.contact_number,
                'date': appointment.date.isoformat(),
                'time': appointment.time.strftime('%H:%M:%S'),
                'purpose': appointment.purpose,
                'status': 'Approved',
                '_save': 'Save',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, 'Approved')
