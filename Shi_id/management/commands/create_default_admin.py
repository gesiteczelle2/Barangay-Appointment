import getpass
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction

from Shi_id.models import UserSecuritySettings


def _create_sqlite_backup():
    if connection.vendor != 'sqlite':
        raise CommandError(
            'Automatic backup is configured for this SQLite project. '
            'Back up the database with its database-specific tools before resetting the admin password.'
        )

    database_name = str(connection.settings_dict['NAME'])
    if database_name == ':memory:' or 'mode=memory' in database_name:
        return None

    backup_directory = Path(settings.BASE_DIR) / 'backups'
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / (
        f"db_before_admin_password_reset_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.sqlite3"
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
    help = 'Create the initial admin account without changing an existing admin.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset-existing',
            action='store_true',
            help=(
                'Reset the existing admin superuser password after creating a verified SQLite backup '
                'and require a password change at next login.'
            ),
        )
        parser.add_argument(
            '--noinput',
            action='store_true',
            help='Require DEFAULT_ADMIN_PASSWORD in the environment instead of prompting.',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = 'admin'
        existing = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if existing:
            if not (existing.is_staff and existing.is_superuser):
                raise CommandError(
                    'An account named admin already exists but is not a staff superuser. '
                    'No account or password was changed.'
                )
            if options['reset_existing']:
                password = self._get_password(options['noinput'])
                self._validate_password(password, existing)
                backup_path = _create_sqlite_backup()
                try:
                    with transaction.atomic():
                        existing.set_password(password)
                        existing.save(update_fields=['password'])
                        UserSecuritySettings.objects.update_or_create(
                            user=existing,
                            defaults={'must_change_password': True},
                        )
                except IntegrityError as error:
                    raise CommandError(
                        f'Could not reset the existing admin password: {error}'
                    ) from error
                if backup_path:
                    self.stdout.write(f'Verified database backup: {backup_path}')
                else:
                    self.stdout.write('Running on an in-memory database; no persistent backup was needed.')
                self.stdout.write(self.style.SUCCESS(
                    'Reset the existing admin password. The account must change it after its next login.'
                ))
                return
            self.stdout.write(self.style.WARNING(
                'The admin superuser already exists; its password and permissions were left unchanged.'
            ))
            return

        password = self._get_password(options['noinput'])

        user = User(username=username, email=os.environ.get('DEFAULT_ADMIN_EMAIL', '').strip())
        self._validate_password(password, user)

        try:
            with transaction.atomic():
                user = User.objects.create_superuser(
                    username=username,
                    email=user.email,
                    password=password,
                )
                UserSecuritySettings.objects.create(
                    user=user,
                    must_change_password=True,
                )
        except IntegrityError as error:
            raise CommandError(f'Could not create the initial admin account: {error}') from error

        self.stdout.write(self.style.SUCCESS(
            'Created the admin superuser. The account must change its password after its first login.'
        ))

    @staticmethod
    def _get_password(noinput):
        password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        if not password and noinput:
            raise CommandError(
                'Set DEFAULT_ADMIN_PASSWORD or run without --noinput to enter it securely.'
            )
        if not password:
            password = getpass.getpass('Enter the initial admin password: ')
            confirmation = getpass.getpass('Confirm the initial admin password: ')
            if password != confirmation:
                raise CommandError('The entered passwords do not match.')
        return password

    @staticmethod
    def _validate_password(password, user):
        try:
            validate_password(password, user=user)
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from error
