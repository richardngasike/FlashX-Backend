from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(responses={200: dict})
    def get(self, request):
        db_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:  # pragma: no cover - reported, not raised
            db_ok = False
        from apps.live.livekit import is_configured as livekit_configured
        from apps.music.services import provider_names
        from apps.notifications.push import is_enabled as push_enabled

        video = livekit_configured()
        return Response(
            {
                "status": "ok" if db_ok else "degraded",
                "database": db_ok,
                "push": push_enabled(),
                # What this deployment can do; the app reads it to explain missing setup precisely.
                "features": {"live": video, "calls": video, "push": push_enabled(), "music": provider_names()},
                "api_version": API_VERSION,
            },
            status=200 if db_ok else 503,
        )


# Bumped when the app needs endpoints that older deployments lack (live, calls, sounds).
API_VERSION = 3
