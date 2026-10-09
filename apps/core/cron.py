"""
Scheduled maintenance over HTTP.

Serverless hosts such as Vercel have no shell or crontab, so the jobs that
run as management commands elsewhere are exposed here and triggered by
Vercel Cron (see vercel.json). Requests must carry
"Authorization: Bearer <CRON_SECRET>".
"""

import hmac
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.media.services import purge_orphans
from apps.stories.services import purge_expired

logger = logging.getLogger(__name__)


def _authorized(request) -> bool:
    secret = settings.CRON_SECRET
    if not secret:
        return False
    header = request.headers.get("Authorization", "")
    return hmac.compare_digest(header.encode(), f"Bearer {secret}".encode())


@extend_schema(exclude=True)
class MaintenanceCronView(APIView):
    """Daily: delete expired stories and uploads that were never used."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        if not settings.CRON_SECRET:
            return Response(
                {
                    "success": False,
                    "error": {"code": "cron_disabled", "message": "CRON_SECRET is not set.", "details": None},
                },
                status=503,
            )
        if not _authorized(request):
            return Response(
                {
                    "success": False,
                    "error": {"code": "not_authenticated", "message": "Invalid cron credentials.", "details": None},
                },
                status=401,
            )
        stories = purge_expired()
        uploads = purge_orphans(older_than_hours=24)
        logger.info("Maintenance: purged %s expired stories, %s orphaned uploads", stories, uploads)
        return Response({"expired_stories_deleted": stories, "orphaned_uploads_deleted": uploads})
