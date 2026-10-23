import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from Shi_id.models import Appointment, ResidentProfile


def _create_sqlite_backup():
    if connection.vendor != 'sqlite':
        raise CommandError(
            'Automatic backup is configured for this SQLite project. '
            'Back up the database with its database-specific tools before deletion.'
        )

    database_name = str(connection.settings_dict['NAME'])
    if database_name == ':memory:' or 'mode=memory' in database_name:
        return None

    backup_directory = Path(settings.BASE_DIR) / 'backups'
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / (
        f"db_before_resident_deletion_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.sqlite3"
    )

    source = sqlite3.connect(database_name)
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
        integrity = destination.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise CommandError(f'Database backup failed its integrity check: {integrity}')
    finally:
        destination.close()
        source.close()
    return backup_path


class Command(BaseCommand):
    help = 'Back up the SQLite database and delete resident-only user accounts.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--noinput',
            action='store_true',
            help='Do not ask for confirmation before deleting resident accounts.',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        resident_users = User.objects.filter(is_staff=False, is_superuser=False)
        resident_count = resident_users.count()
        profile_count = ResidentProfile.objects.filter(user__in=resident_users).count()
        owned_appointment_count = Appointment.objects.filter(resident__in=resident_users).count()

        admin_users = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True))
        admin_snapshot = {
            user.pk: {
                'password': user.password,
                'email': user.email,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'is_superuser': user.is_superuser,
                'groups': tuple(user.groups.values_list('pk', flat=True)),
                'permissions': tuple(user.user_permissions.values_list('pk', flat=True)),
            }
            for user in admin_users
        }
        if not any(
            user.is_active and user.has_usable_password()
            for user in admin_users
        ):
            raise CommandError(
                'Refusing to delete accounts: no active administrator/staff account '
                'with a usable password would remain.'
            )

        if resident_count and not options['noinput']:
            answer = input(
                f'Delete {resident_count} resident account(s) and {profile_count} linked profile(s)? [y/N] '
            )
            if answer.strip().lower() not in {'y', 'yes'}:
                self.stdout.write('No accounts were deleted.')
                return

        backup_path = _create_sqlite_backup()
        appointment_count = Appointment.objects.count()

        with transaction.atomic():
            resident_users.delete()
            if Appointment.objects.count() != appointment_count:
                raise CommandError('Appointment records changed unexpectedly; transaction rolled back.')
            if Appointment.objects.filter(resident__isnull=False).exclude(
                resident__is_staff=True,
            ).exclude(
                resident__is_superuser=True,
            ).exists():
                raise CommandError('Resident-owned appointments remain after deletion; transaction rolled back.')
            for user in User.objects.filter(pk__in=admin_snapshot):
                previous = admin_snapshot[user.pk]
                current = {
                    'password': user.password,
                    'email': user.email,
                    'is_active': user.is_active,
                    'is_staff': user.is_staff,
                    'is_superuser': user.is_superuser,
                    'groups': tuple(user.groups.values_list('pk', flat=True)),
                    'permissions': tuple(user.user_permissions.values_list('pk', flat=True)),
                }
                if current != previous:
                    raise CommandError(
                        f'Administrator account {user.get_username()} changed unexpectedly; transaction rolled back.'
                    )

            remaining_admins = User.objects.filter(
                Q(is_staff=True) | Q(is_superuser=True),
                is_active=True,
            )
            if not any(user.has_usable_password() for user in remaining_admins):
                raise CommandError(
                    'No active administrator/staff account remains; transaction rolled back.'
                )

        if backup_path:
            self.stdout.write(f'Verified database backup: {backup_path}')
        else:
            self.stdout.write('Running on an in-memory database; no persistent backup was needed.')
        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {resident_count} resident account(s), '
                f'{profile_count} linked profile(s); preserved {appointment_count} appointment(s).'
            )
        )
