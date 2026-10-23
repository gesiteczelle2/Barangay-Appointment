
"""
Django settings for SIA_Project.

Barangay Appointment System
Database: Supabase PostgreSQL
Deployment: Vercel
"""

import os
from pathlib import Path
from dotenv import load_dotenv


# =====================================================
# BASE DIRECTORY
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# Load local environment variables.
# On Vercel, configure variables in Project Settings.
load_dotenv(BASE_DIR / ".env")


# =====================================================
# SECURITY SETTINGS
# =====================================================

# Required in both development and production.
# Never hardcode your real secret key.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# False by default for production safety.
DEBUG = os.getenv(
    "DJANGO_DEBUG", "False"
).lower() == "true"

# Allowed domains.
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "testserver",
    ".vercel.app",
]

# Additional production host, if configured.
EXTRA_ALLOWED_HOSTS = os.getenv(
    "DJANGO_ALLOWED_HOSTS", ""
)

if EXTRA_ALLOWED_HOSTS:
    ALLOWED_HOSTS += [
        host.strip()
        for host in EXTRA_ALLOWED_HOSTS.split(",")
        if host.strip()
    ]

# Trusted HTTPS origins for Django forms and CSRF.
CSRF_TRUSTED_ORIGINS = [
    "https://*.vercel.app",
]

EXTRA_CSRF_ORIGINS = os.getenv(
    "DJANGO_CSRF_TRUSTED_ORIGINS", ""
)

if EXTRA_CSRF_ORIGINS:
    CSRF_TRUSTED_ORIGINS += [
        origin.strip()
        for origin in EXTRA_CSRF_ORIGINS.split(",")
        if origin.strip()
    ]

# Security options for production.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

SECURE_SSL_REDIRECT = not DEBUG

X_FRAME_OPTIONS = "DENY"


# =====================================================
# LOGIN AND LOGOUT SETTINGS
# =====================================================

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/"

LOGIN_URL = "login"


# =====================================================
# INSTALLED APPLICATIONS
# =====================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Barangay Appointment System
    "Shi_id",
]


# =====================================================
# MIDDLEWARE
# =====================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # Static file serving in production
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    # Existing Barangay middleware
    "Shi_id.middleware.RequirePasswordChangeMiddleware",
]


# =====================================================
# URL CONFIGURATION
# =====================================================

ROOT_URLCONF = "SIA_Project.urls"


# =====================================================
# TEMPLATES
# =====================================================

TEMPLATES = [
    {
        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

        "DIRS": [],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =====================================================
# WSGI APPLICATION
# =====================================================

WSGI_APPLICATION = "SIA_Project.wsgi.application"


# =====================================================
# DATABASE CONFIGURATION
# SUPABASE POSTGRESQL
# =====================================================

DATABASES = {

    "default": {
        "ENGINE": "django.db.backends.postgresql",

        "NAME": os.environ["DB_NAME"],

        "USER": os.environ["DB_USER"],

        "PASSWORD": os.environ["DB_PASSWORD"],

        "HOST": os.environ["DB_HOST"],

        "PORT": os.getenv("DB_PORT", "5432"),

        "OPTIONS": {
            "sslmode": "require",
            "connect_timeout": 10,
        },

        # Recommended for serverless connections.
        "CONN_MAX_AGE": 0,
    },
}

# Optional SQLite connection for local migration only.
# Never use this as the production database.
if DEBUG:
    DATABASES["legacy"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }


# =====================================================
# PASSWORD VALIDATION
# =====================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME":
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME":
            "django.contrib.auth.password_validation.MinimumLengthValidator",

        "OPTIONS": {
            "min_length": 8,
        },
    },
    {
        "NAME":
            "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME":
            "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# =====================================================
# INTERNATIONALIZATION
# =====================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Manila"

USE_I18N = True

USE_TZ = True


# =====================================================
# STATIC FILES
# =====================================================

STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

# Serve compressed static files using WhiteNoise.
STORAGES = {
    "default": {
        "BACKEND":
            "django.core.files.storage.FileSystemStorage",
    },

    "staticfiles": {
        "BACKEND":
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# =====================================================
# MEDIA FILES
# =====================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"

# Note:
# Vercel's filesystem is not persistent.
# Use Supabase Storage or another object-storage
# service for user-uploaded files in production.


# =====================================================
# EMAIL CONFIGURATION
# =====================================================

# Console email backend is useful for local testing.
# It does not send real emails to recipients.
EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)


# =====================================================
# DEFAULT PRIMARY KEY
# =====================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
