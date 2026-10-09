"""OpenAPI description of FlashX's JWT authentication (Bearer access token)."""

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class FlashXJWTScheme(SimpleJWTScheme):
    target_class = "apps.users.authentication.FlashXJWTAuthentication"
    name = "jwtAuth"
