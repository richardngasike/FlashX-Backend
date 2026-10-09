"""
FlashX base settings. Environment-specific modules import from here.
All secrets are read from the environment (see .env.example).
"""

from datetime import timedelta
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.users",
    "apps.follows",
    "apps.media",
    "apps.posts",
    "apps.comments",
    "apps.likes",
    "apps.saves",
    "apps.stories",
    "apps.reels",
    "apps.messaging",
    "apps.notifications",
    "apps.search",
    "apps.reports",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ADMIN_URL = config("ADMIN_URL", default="admin/")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        config("DATABASE_URL"),
        conn_max_age=config("DB_CONN_MAX_AGE", default=60, cast=int),
        conn_health_checks=True,
    )
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="Africa/Nairobi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.users.authentication.FlashXJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": ("apps.core.renderers.FlashXJSONRenderer",),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.flashx_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": config("THROTTLE_ANON", default="60/min"),
        "user": config("THROTTLE_USER", default="600/min"),
        "auth": config("THROTTLE_AUTH", default="10/min"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="5/hour"),
        "upload": config("THROTTLE_UPLOAD", default="60/hour"),
        "content_create": config("THROTTLE_CONTENT_CREATE", default="60/hour"),
        "message_send": config("THROTTLE_MESSAGE_SEND", default="120/min"),
        "report": config("THROTTLE_REPORT", default="30/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_TOKEN_LIFETIME", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_TOKEN_LIFETIME", default=30, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    # Blank or unset falls back to SECRET_KEY.
    "SIGNING_KEY": config("JWT_SIGNING_KEY", default="") or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "FlashX API",
    "DESCRIPTION": "REST API for the FlashX social platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {"ResourceTypeEnum": "apps.media.models.ResourceType"},
}

# ---------------------------------------------------------------------------
# CORS / CSRF
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
CORS_ALLOW_CREDENTIALS = False
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# ---------------------------------------------------------------------------
# Cloudinary
# ---------------------------------------------------------------------------
CLOUDINARY = {
    "CLOUD_NAME": config("CLOUDINARY_CLOUD_NAME", default=""),
    "API_KEY": config("CLOUDINARY_API_KEY", default=""),
    "API_SECRET": config("CLOUDINARY_API_SECRET", default=""),
    # Root folder all FlashX assets live under; per-environment separation.
    "ROOT_FOLDER": config("CLOUDINARY_ROOT_FOLDER", default="flashx"),
    # When True, every registered upload is re-checked against the Cloudinary
    # Admin API so size/duration/format come from Cloudinary, not the client.
    "VERIFY_WITH_ADMIN_API": config("CLOUDINARY_VERIFY_WITH_ADMIN_API", default=True, cast=bool),
    # Signed upload parameters expire after this many seconds.
    "SIGNATURE_TTL_SECONDS": config("CLOUDINARY_SIGNATURE_TTL_SECONDS", default=3600, cast=int),
}

# Upload limits enforced server-side (bytes / seconds).
FLASHX_MEDIA_LIMITS = {
    "IMAGE_MAX_BYTES": config("MEDIA_IMAGE_MAX_BYTES", default=15 * 1024 * 1024, cast=int),
    "VIDEO_MAX_BYTES": config("MEDIA_VIDEO_MAX_BYTES", default=150 * 1024 * 1024, cast=int),
    "IMAGE_FORMATS": ["jpg", "jpeg", "png", "webp", "heic", "heif", "gif"],
    "VIDEO_FORMATS": ["mp4", "mov", "webm", "m4v", "3gp"],
    "POST_MAX_ITEMS": config("POST_MAX_MEDIA_ITEMS", default=10, cast=int),
    "POST_VIDEO_MAX_SECONDS": config("POST_VIDEO_MAX_SECONDS", default=600, cast=int),
    "STORY_VIDEO_MAX_SECONDS": config("STORY_VIDEO_MAX_SECONDS", default=60, cast=int),
    "REEL_MAX_SECONDS": config("REEL_MAX_SECONDS", default=180, cast=int),
    "MESSAGE_VIDEO_MAX_SECONDS": config("MESSAGE_VIDEO_MAX_SECONDS", default=300, cast=int),
}

FLASHX = {
    "STORY_LIFETIME_HOURS": config("STORY_LIFETIME_HOURS", default=24, cast=int),
    "ONLINE_WINDOW_SECONDS": config("ONLINE_WINDOW_SECONDS", default=300, cast=int),
    "LAST_SEEN_UPDATE_INTERVAL_SECONDS": 60,
    "REPORT_AUTO_HIDE_THRESHOLD": config("REPORT_AUTO_HIDE_THRESHOLD", default=0, cast=int),
    "PASSWORD_RESET_URL": config("PASSWORD_RESET_URL", default="flashx://reset-password?uid={uid}&token={token}"),
    "CAPTION_MAX_LENGTH": 2200,
    "COMMENT_MAX_LENGTH": 1000,
    "MESSAGE_MAX_LENGTH": 4000,
}

# Shared secret for /api/cron/* endpoints. Vercel Cron sends it as
# "Authorization: Bearer <CRON_SECRET>"; the endpoints refuse to run without it.
CRON_SECRET = config("CRON_SECRET", default="")

# ---------------------------------------------------------------------------
# Email (password reset)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="FlashX <no-reply@flashx.app>")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
