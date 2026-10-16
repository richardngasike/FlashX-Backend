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
    "apps.blocks",
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
    "apps.ads",
    "apps.music",
    "apps.live",
    "apps.calls",
    "apps.gifs",
    "apps.wallet",
    "apps.payments",
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
    "apps.core.middleware.DatabaseRetryMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.core.middleware.AdminLoginThrottleMiddleware",
]

# "default" is per-process memory (fine for short-lived lookups). "security"
# holds rate limits and sign-in lockouts; production points it at the
# database so the counts are shared by every server instance.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "flashx-default"},
    "security": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "flashx-security"},
}

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
# Fail fast on an unreachable database instead of hanging the request.
if "postgresql" in DATABASES["default"].get("ENGINE", ""):
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = config(
        "DB_CONNECT_TIMEOUT", default=10, cast=int
    )

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
        "apps.core.throttles.FlashXAnonRateThrottle",
        "apps.core.throttles.FlashXUserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": config("THROTTLE_ANON", default="60/min"),
        "user": config("THROTTLE_USER", default="600/min"),
        # Per IP. Mobile carriers put many people behind one address, so these
        # are generous; per-account lockout is what stops password guessing.
        "login": config("THROTTLE_LOGIN", default="30/min"),
        "register": config("THROTTLE_REGISTER", default="30/hour"),
        "token_refresh": config("THROTTLE_TOKEN_REFRESH", default="60/min"),
        "lookup": config("THROTTLE_LOOKUP", default="60/min"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="20/hour"),
        "upload": config("THROTTLE_UPLOAD", default="60/hour"),
        "content_create": config("THROTTLE_CONTENT_CREATE", default="60/hour"),
        "message_send": config("THROTTLE_MESSAGE_SEND", default="120/min"),
        "report": config("THROTTLE_REPORT", default="30/hour"),
        "reaction": config("THROTTLE_REACTION", default="120/min"),
        "typing": config("THROTTLE_TYPING", default="40/min"),
        "gif_search": config("THROTTLE_GIF_SEARCH", default="60/min"),
        "live_action": config("THROTTLE_LIVE_ACTION", default="60/min"),
        "payment": config("THROTTLE_PAYMENT", default="10/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# Shared secret the FlashX website sends from its server-side proxy so anonymous
# rate limits apply per visitor, not to the website's server IP. Blank = off.
WEB_PROXY_SECRET = config("WEB_PROXY_SECRET", default="")

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
    # Tokens carry a fingerprint of the password hash: changing or resetting
    # the password ends every session at once, access tokens included.
    "CHECK_REVOKE_TOKEN": True,
    "REVOKE_TOKEN_CLAIM": "hash_password",
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
# Coins and live gifts. Buying coins isn't open yet; people start with a welcome bonus.
WALLET = {
    "WELCOME_COINS": config("WALLET_WELCOME_COINS", default=500, cast=int),
    "KSH_PER_COIN": config("WALLET_KSH_PER_COIN", default=1.0, cast=float),
    "MAX_GIFT_QUANTITY": config("WALLET_MAX_GIFT_QUANTITY", default=99, cast=int),
}

PAYMENTS = {
    # Off before publishing on Google Play (Play billing is required there for coins and badges).
    "DIRECT_ENABLED": config("PAYMENTS_DIRECT_ENABLED", default=True, cast=bool),
    "VERIFICATION_PRICE_KSH": config("VERIFICATION_PRICE_KSH", default=500, cast=int),
}

# Safaricom Daraja (Lipa na M-PESA Online). Payments stay "coming soon" until
# the key, secret, short code and passkey are set. See apps/payments/mpesa.py.
MPESA = {
    "ENVIRONMENT": config("MPESA_ENVIRONMENT", default="sandbox"),
    "CONSUMER_KEY": config("MPESA_CONSUMER_KEY", default=""),
    "CONSUMER_SECRET": config("MPESA_CONSUMER_SECRET", default=""),
    "SHORTCODE": config("MPESA_SHORTCODE", default=""),
    "PASSKEY": config("MPESA_PASSKEY", default=""),
    "TILL_NUMBER": config("MPESA_TILL_NUMBER", default=""),
    "CALLBACK_BASE_URL": config("MPESA_CALLBACK_BASE_URL", default=""),
    "CALLBACK_TOKEN": config("MPESA_CALLBACK_TOKEN", default=""),
}

FLASHX_MEDIA_LIMITS = {
    "IMAGE_MAX_BYTES": config("MEDIA_IMAGE_MAX_BYTES", default=15 * 1024 * 1024, cast=int),
    "VIDEO_MAX_BYTES": config("MEDIA_VIDEO_MAX_BYTES", default=150 * 1024 * 1024, cast=int),
    "IMAGE_FORMATS": ["jpg", "jpeg", "png", "webp", "heic", "heif", "gif"],
    "VIDEO_FORMATS": ["mp4", "mov", "webm", "m4v", "3gp"],
    # Messages only: audio files and voice notes (stored as Cloudinary "video") and documents.
    "AUDIO_FORMATS": ["m4a", "aac", "mp3", "wav", "ogg", "oga", "opus", "amr", "flac"],
    "FILE_FORMATS": [
        "pdf",
        "doc",
        "docx",
        "txt",
        "rtf",
        "odt",
        "xls",
        "xlsx",
        "csv",
        "ods",
        "ppt",
        "pptx",
        "odp",
        "zip",
        "rar",
        "7z",
        "json",
        "md",
    ],
    "FILE_MAX_BYTES": config("MEDIA_FILE_MAX_BYTES", default=25 * 1024 * 1024, cast=int),
    "POST_MAX_ITEMS": config("POST_MAX_MEDIA_ITEMS", default=10, cast=int),
    "POST_VIDEO_MAX_SECONDS": config("POST_VIDEO_MAX_SECONDS", default=600, cast=int),
    "STORY_VIDEO_MAX_SECONDS": config("STORY_VIDEO_MAX_SECONDS", default=60, cast=int),
    # Uploaded or recorded sounds (the file; a sound itself plays at most SOUND_CLIP_MAX_SECONDS of it).
    "SOUND_MAX_SECONDS": config("SOUND_MAX_SECONDS", default=600, cast=int),
    "SOUND_CLIP_MAX_SECONDS": config("SOUND_CLIP_MAX_SECONDS", default=180, cast=int),
    # GIF creator: largest finished GIF, longest video segment, most photos.
    "GIF_MAX_BYTES": config("GIF_MAX_BYTES", default=8 * 1024 * 1024, cast=int),
    "GIF_VIDEO_MAX_SECONDS": config("GIF_VIDEO_MAX_SECONDS", default=10, cast=int),
    "GIF_MAX_FRAMES": config("GIF_MAX_FRAMES", default=30, cast=int),
    "REEL_MAX_SECONDS": config("REEL_MAX_SECONDS", default=360, cast=int),
    "MESSAGE_VIDEO_MAX_SECONDS": config("MESSAGE_VIDEO_MAX_SECONDS", default=300, cast=int),
    "MESSAGE_AUDIO_MAX_SECONDS": config("MESSAGE_AUDIO_MAX_SECONDS", default=1800, cast=int),
}

FLASHX = {
    "STORY_LIFETIME_HOURS": config("STORY_LIFETIME_HOURS", default=24, cast=int),
    "ONLINE_WINDOW_SECONDS": config("ONLINE_WINDOW_SECONDS", default=150, cast=int),
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
# Push notifications (Firebase Cloud Messaging). Empty key = push off.
# ---------------------------------------------------------------------------
FCM = {"SERVICE_ACCOUNT_JSON": config("FCM_SERVICE_ACCOUNT_JSON", default="")}

# ---------------------------------------------------------------------------
# Music for posts and reels (Jamendo, Creative Commons). Empty client id = only
# original FlashX sounds are offered. Sponsored posts make FlashX a commercial
# app, so NonCommercial tracks are excluded while COMMERCIAL_USE is on and the
# Jamendo API must be used under a commercial agreement with Jamendo.
# ---------------------------------------------------------------------------
MUSIC = {
    "JAMENDO_CLIENT_ID": config("JAMENDO_CLIENT_ID", default=""),
    "COMMERCIAL_USE": config("MUSIC_COMMERCIAL_USE", default=True, cast=bool),
    # Openverse works without credentials; registering raises the rate limit.
    "OPENVERSE_ENABLED": config("MUSIC_OPENVERSE", default=True, cast=bool),
    "OPENVERSE_CLIENT_ID": config("OPENVERSE_CLIENT_ID", default=""),
    "OPENVERSE_CLIENT_SECRET": config("OPENVERSE_CLIENT_SECRET", default=""),
}

# GIF and sticker search in chats (GIPHY preferred, Tenor as an alternative).
# Both empty = the GIF picker reports that search is unavailable.
GIFS = {
    "GIPHY_API_KEY": config("GIPHY_API_KEY", default=""),
    "TENOR_API_KEY": config("TENOR_API_KEY", default=""),
    "RATING": config("GIF_RATING", default="pg-13"),
}

# ---------------------------------------------------------------------------
# Live video (LiveKit Cloud or self-hosted). Empty values = live is off.
# ---------------------------------------------------------------------------
# Guests on screen besides the host (99 = rooms of up to 100 people; LiveKit plan limits apply).
LIVE_MAX_GUESTS = config("LIVE_MAX_GUESTS", default=99, cast=int)

LIVEKIT = {
    "URL": config("LIVEKIT_URL", default=""),
    "API_KEY": config("LIVEKIT_API_KEY", default=""),
    "API_SECRET": config("LIVEKIT_API_SECRET", default=""),
}

# ---------------------------------------------------------------------------
# Email (password reset)
# ---------------------------------------------------------------------------
EMAIL_HOST = config("EMAIL_HOST", default="").strip()
# SMTP as soon as a host is configured; the console backend (prints to the log,
# delivers nothing) only when no SMTP server is set at all.
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if EMAIL_HOST
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="").strip()
# Gmail shows app passwords in groups of four ("abcd efgh ..."); the spaces are not part of it.
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="").replace(" ", "")
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=EMAIL_PORT == 465, cast=bool)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=not EMAIL_USE_SSL, cast=bool)
# Never let a slow SMTP server hold a request until the function times out.
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=15, cast=int)
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
