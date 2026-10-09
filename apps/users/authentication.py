from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication


class FlashXJWTAuthentication(JWTAuthentication):
    """JWT auth that also records presence (throttled to one write a minute)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _ = result
            now = timezone.now()
            interval = timedelta(seconds=settings.FLASHX["LAST_SEEN_UPDATE_INTERVAL_SECONDS"])
            if not user.last_seen_at or now - user.last_seen_at > interval:
                type(user).objects.filter(pk=user.pk).update(last_seen_at=now)
                user.last_seen_at = now
        return result
