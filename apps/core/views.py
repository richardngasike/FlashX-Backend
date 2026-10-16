import logging

from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


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
        except Exception as exc:  # pragma: no cover - reported, not raised
            logger.error("Health check: database unavailable: %s: %s", type(exc).__name__, exc)
            db_ok = False
        from apps.live.livekit import is_configured as livekit_configured
        from apps.music.services import catalogue_status, provider_names
        from apps.notifications.push import is_enabled as push_enabled
        from apps.users.services import email_delivery_enabled

        pending = _pending_migrations() if db_ok else None
        healthy = db_ok and not pending
        body = {
            "status": "ok" if healthy else "degraded",
            "database": db_ok,
            # Unapplied migrations make most requests fail with 500s: run `manage.py migrate`.
            "migrations_pending": pending,
            "api_version": API_VERSION,
        }
        # Which services are configured is for operators only:
        # send "Authorization: Bearer <CRON_SECRET>" to see it.
        from apps.core.cron import _authorized

        if _authorized(request):
            video = livekit_configured()
            body["push"] = push_enabled()
            body["features"] = {
                "live": video,
                "calls": video,
                "push": push_enabled(),
                "music": provider_names(),
                "music_status": catalogue_status(),
                "email": email_delivery_enabled(),
            }
        return Response(body, status=200 if healthy else 503)


_schema_current = False


def _pending_migrations() -> int | None:
    """Migrations not yet applied to this database (None if it can't be checked)."""
    global _schema_current
    if _schema_current:
        return 0
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connection)
        count = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except Exception:  # pragma: no cover - reported, not raised
        logger.exception("Could not check migrations")
        return None
    # Once the schema is current it stays current for this process's code.
    _schema_current = count == 0
    return count


# Bumped when the app needs endpoints that older deployments lack (live, calls, sounds).
API_VERSION = 3
