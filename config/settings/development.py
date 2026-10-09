from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK, config

DEBUG = config("DEBUG", default=True, cast=bool)

# Flutter emulators/devices call the API from arbitrary local origins.
CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=True, cast=bool)

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
    "apps.core.renderers.FlashXJSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)
