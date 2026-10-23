from django.shortcuts import redirect
from django.urls import reverse

from .models import UserSecuritySettings


class RequirePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            security_settings = UserSecuritySettings.objects.filter(
                user=request.user,
                must_change_password=True,
            ).only('pk').first()
            if security_settings:
                allowed_paths = {
                    reverse('force_password_change'),
                    reverse('logout'),
                }
                if request.path not in allowed_paths:
                    return redirect('force_password_change')
        return self.get_response(request)
