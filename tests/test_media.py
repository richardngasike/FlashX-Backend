import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.media import cloudinary_service as cld
from apps.media.models import MediaAsset

from .base import FlashXTestCase, png_bytes


class SignedUploadTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user())

    def test_sign_returns_params_scoped_to_user_folder(self):
        data = self.assertOk(self.client.post(reverse("media-sign"), {"purpose": "post", "resource_type": "image"}))
        self.assertTrue(data["public_id"].startswith(f"flashx/u{self.me.id}/post/"))
        self.assertIn("signature", data)
        self.assertNotIn("api_secret", data)
        self.assertNotIn("test-secret", str(data))
        self.assertEqual(data["upload_url"], "https://api.cloudinary.com/v1_1/flashx-test/image/upload")

    def test_sign_rejects_bad_purpose_resource_combo(self):
        self.assertError(self.client.post(reverse("media-sign"), {"purpose": "avatar", "resource_type": "video"}), 415)
        self.assertError(self.client.post(reverse("media-sign"), {"purpose": "reel", "resource_type": "image"}), 415)

    def _register(self, public_id=None, **kw):
        public_id = public_id or f"{cld.folder_for(self.me.id, 'post')}/abc"
        data = {"purpose": "post", "resource_type": "image", "public_id": public_id, "version": "1", "signature": "sig"}
        data.update(kw)
        return self.client.post(reverse("media-register"), data)

    def test_register_uses_cloudinary_metadata(self):
        data = self.assertOk(self._register(bytes=1), 201)
        self.assertEqual(data["bytes"], 1024)  # from Admin API, not the client
        self.assertEqual(data["urls"]["thumbnail"].split("/")[2], "res.cloudinary.com")
        self.assertTrue(MediaAsset.objects.filter(owner=self.me, is_attached=False).exists())
        # Idempotent
        self.assertOk(self._register(), 201)
        self.assertEqual(MediaAsset.objects.count(), 1)

    def test_register_rejects_foreign_folder_and_bad_signature(self):
        other = self.make_user()
        self.assertError(self._register(public_id=f"{cld.folder_for(other.id, 'post')}/x"), 403, "upload_not_owned")
        self.verify_sig.return_value = False
        self.assertError(self._register(), 403, "invalid_upload_signature")

    def test_oversized_upload_is_rejected_and_destroyed(self):
        self.resource_overrides = {"bytes": 500 * 1024 * 1024}
        self.assertError(self._register(), 413, "file_too_large")
        self.destroy.assert_called_once()
        self.assertFalse(MediaAsset.objects.exists())

    def test_overlong_story_video_rejected(self):
        self.resource_overrides = {"duration": 120.0}
        public_id = f"{cld.folder_for(self.me.id, 'story')}/vid"
        self.assertError(
            self._register(public_id=public_id, purpose="story", resource_type="video"), 400, "video_too_long"
        )

    def test_discard_unattached_asset_only(self):
        a = self.make_asset(self.me)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.delete(reverse("media-delete", args=[a.id])), 204)
        self.destroy.assert_called_once_with(a.public_id, "image")
        b = self.make_asset(self.me, is_attached=True)
        self.assertError(self.client.delete(reverse("media-delete", args=[b.id])), 409)
        c = self.make_asset(self.make_user())
        self.assertError(self.client.delete(reverse("media-delete", args=[c.id])), 404)


class DirectUploadTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user())

    def test_image_upload_is_sniffed_and_sent_to_cloudinary(self):
        def fake_upload(fileobj, user_id, purpose, resource_type):
            return {
                "public_id": f"{cld.folder_for(user_id, purpose)}/up",
                "version": "2",
                "format": "png",
                "secure_url": "https://res.cloudinary.com/x.png",
                "bytes": 100,
                "width": 8,
                "height": 8,
                "duration": None,
                "resource_type": resource_type,
            }

        with mock.patch.object(cld, "upload_file", side_effect=fake_upload) as up:
            f = SimpleUploadedFile("avatar.png", png_bytes(), content_type="image/png")
            data = self.assertOk(
                self.client.post(reverse("media-upload"), {"purpose": "avatar", "file": f}, format="multipart"), 201
            )
        up.assert_called_once()
        self.assertEqual(data["purpose"], "avatar")

    def test_spoofed_file_rejected_before_upload(self):
        with mock.patch.object(cld, "upload_file") as up:
            f = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", content_type="image/png")
            self.assertError(
                self.client.post(reverse("media-upload"), {"purpose": "avatar", "file": f}, format="multipart"),
                415,
                "unsupported_file_type",
            )
        up.assert_not_called()

    def test_video_not_allowed_for_avatar(self):
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
        f = SimpleUploadedFile("clip.mp4", mp4, content_type="video/mp4")
        self.assertError(
            self.client.post(reverse("media-upload"), {"purpose": "avatar", "file": f}, format="multipart"), 415
        )


class OrphanPurgeTests(FlashXTestCase):
    def test_purges_only_old_unattached(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        owner = self.make_user()
        old = self.make_asset(owner)
        attached = self.make_asset(owner, is_attached=True)
        fresh = self.make_asset(owner)
        MediaAsset.objects.filter(pk__in=[old.pk, attached.pk]).update(created_at=timezone.now() - timedelta(days=2))
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge_orphan_media", stdout=io.StringIO())
        self.assertEqual(set(MediaAsset.objects.values_list("pk", flat=True)), {attached.pk, fresh.pk})
        self.destroy.assert_called_once_with(old.public_id, "image")
