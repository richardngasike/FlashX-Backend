from .base import *  # noqa: F401,F403
from .base import CLOUDINARY, LOGGING, MIDDLEWARE, REST_FRAMEWORK, SIMPLE_JWT

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# Tests never serve static files, so collectstatic is not required.
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

FCM = {"SERVICE_ACCOUNT_JSON": ""}
CLOUDINARY.update({"CLOUD_NAME": "flashx-test", "API_KEY": "test-key", "API_SECRET": "test-secret"})

# Throttling is exercised by dedicated tests; keep it out of the way elsewhere.
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {k: "10000/min" for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]}

SIMPLE_JWT["SIGNING_KEY"] = "flashx-test-signing-key-0123456789abcdef"

# Many tests assert 4xx responses on purpose; keep their request logs out of the output.
LOGGING["loggers"]["django.request"] = {"level": "CRITICAL"}
LOGGING["loggers"]["apps"] = {"level": "WARNING"}
