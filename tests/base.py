import io
import itertools
from unittest import mock

from django.contrib.auth import get_user_model
from PIL import Image
from rest_framework.test import APITestCase

from apps.media import cloudinary_service as cld
from apps.media.models import MediaAsset

User = get_user_model()
_seq = itertools.count(1)
PASSWORD = "Str0ng-pass-123"


def png_bytes(size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGB", size, (190, 255, 60)).save(buf, format="PNG")
    return buf.getvalue()


class FlashXTestCase(APITestCase):
    """Cloudinary is stubbed: no network, but every call is observable."""

    def setUp(self):
        super().setUp()
        self.destroy = mock.patch.object(cld, "destroy", return_value=True).start()
        self.fetch = mock.patch.object(cld, "fetch_resource", side_effect=self._fake_resource).start()
        self.verify_sig = mock.patch.object(cld, "verify_response_signature", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.resource_overrides = {}

    def _fake_resource(self, public_id, resource_type):
        meta = {
            "public_id": public_id,
            "version": "1",
            "resource_type": resource_type,
            "format": "mp4" if resource_type == "video" else "jpg",
            "secure_url": f"https://res.cloudinary.com/flashx-test/{resource_type}/upload/{public_id}",
            "bytes": 1024,
            "width": 1080,
            "height": 1920 if resource_type == "video" else 1080,
            "duration": 12.0 if resource_type == "video" else None,
        }
        meta.update(self.resource_overrides)
        return meta

    # -- factories -------------------------------------------------------
    def make_user(self, username=None, **extra):
        n = next(_seq)
        username = username or f"user{n}"
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            full_name=extra.pop("full_name", f"User {n}"),
            password=PASSWORD,
            **extra,
        )

    def auth(self, user):
        self.client.force_authenticate(user)
        return user

    def make_asset(self, owner, purpose="post", resource_type="image", **extra):
        n = next(_seq)
        public_id = f"{cld.folder_for(owner.id, purpose)}/asset{n}"
        return MediaAsset.objects.create(
            owner=owner,
            purpose=purpose,
            resource_type=resource_type,
            public_id=public_id,
            secure_url=f"https://res.cloudinary.com/flashx-test/{resource_type}/upload/{public_id}",
            bytes=1000,
            width=1080,
            height=1080,
            duration=10.0 if resource_type == "video" else None,
            **extra,
        )

    def make_post(self, author, caption="Hello FlashX", **kw):
        from apps.posts.services import create_post

        return create_post(author, caption=caption, **kw)

    # -- assertions ------------------------------------------------------
    def assertOk(self, response, status=200):
        self.assertEqual(response.status_code, status, getattr(response, "data", response.content))
        if status != 204:
            self.assertTrue(response.json()["success"])
            return response.json()["data"]
        return None

    def assertError(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertFalse(body["success"])
        if code:
            self.assertEqual(body["error"]["code"], code, body)
        return body["error"]
