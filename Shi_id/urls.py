from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('book/', views.book_appointment, name='book_appointment'),
    path('availability/', views.appointment_availability, name='appointment_availability'),
    path('locations/barangays/', views.location_barangays, name='location_barangays'),
    path('locations/puroks/', views.location_puroks, name='location_puroks'),
    path('profile/', views.resident_profile, name='resident_profile'),
    path('account/change-password/', views.force_password_change, name='force_password_change'),
    path('appointments/<str:appointment_id>/', views.appointment_detail, name='appointment_detail'),
    path('appointments/<str:appointment_id>/cancel/', views.cancel_appointment, name='cancel_appointment'),
    path('staff/schedule/', views.schedule_management, name='schedule_management'),
    path('staff/residents/', views.resident_management, name='resident_management'),
    path('staff/services/', views.service_management, name='service_management'),
    path('staff/reports/', views.reports, name='reports'),
    path('staff/reports/export.csv', views.export_appointments_csv, name='export_appointments_csv'),
    path(
        'staff/appointments/<str:appointment_id>/status/',
        views.update_appointment_status,
        name='update_appointment_status',
    ),
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='Shi_id/login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='home'), name='logout'),
]