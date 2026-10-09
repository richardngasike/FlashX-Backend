from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.media.models import MediaAsset
from apps.stories.models import Story
from apps.stories.services import create_story

from .base import FlashXTestCase

URL = "/api/cron/maintenance/"


class MaintenanceCronTests(FlashXTestCase):
    def test_route_has_trailing_slash_so_cron_never_redirects(self):
        self.assertEqual(reverse("cron-maintenance"), URL)

    @override_settings(CRON_SECRET="")
    def test_disabled_without_secret(self):
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(URL, HTTP_AUTHORIZATION="Bearer anything")
        self.assertError(response, 503, "cron_disabled")

    @override_settings(CRON_SECRET="s3cret-value-for-tests")
    def test_rejects_missing_or_wrong_token(self):
        self.assertError(self.client.get(URL), 401, "not_authenticated")
        self.assertError(self.client.get(URL, HTTP_AUTHORIZATION="Bearer wrong"), 401)
        self.assertError(self.client.get(URL, HTTP_AUTHORIZATION="s3cret-value-for-tests"), 401)

    @override_settings(CRON_SECRET="s3cret-value-for-tests")
    def test_purges_expired_stories_and_orphans(self):
        owner = self.make_user()
        live = create_story(owner, media_id=self.make_asset(owner, "story").id)
        old = create_story(owner, media_id=self.make_asset(owner, "story").id)
        Story.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        orphan = self.make_asset(owner)
        fresh = self.make_asset(owner)
        MediaAsset.objects.filter(pk=orphan.pk).update(created_at=timezone.now() - timedelta(days=2))

        with self.captureOnCommitCallbacks(execute=True):
            data = self.assertOk(self.client.get(URL, HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests"))
        self.assertEqual(data, {"expired_stories_deleted": 1, "orphaned_uploads_deleted": 1})
        self.assertTrue(Story.objects.filter(pk=live.pk).exists())
        self.assertFalse(Story.objects.filter(pk=old.pk).exists())
        self.assertFalse(MediaAsset.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(MediaAsset.objects.filter(pk=fresh.pk).exists())
        # Cloudinary files for the expired story and the orphan are removed.
        self.assertEqual(self.destroy.call_count, 2)

        # Idempotent: a duplicate delivery does nothing.
        self.assertEqual(
            self.assertOk(self.client.get(URL, HTTP_AUTHORIZATION="Bearer s3cret-value-for-tests")),
            {"expired_stories_deleted": 0, "orphaned_uploads_deleted": 0},
        )
