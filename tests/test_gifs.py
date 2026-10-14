import io
from unittest import mock

from django.urls import reverse
from PIL import Image

from apps.gifs import services as gifs
from apps.gifs.models import Gif
from apps.media import cloudinary_service as cld
from apps.media.models import MediaAsset

from .base import FlashXTestCase


def png(color=(200, 40, 40), size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class GifCreatorTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("gifmaker"))
        self.stored = mock.patch.object(cld, "store_remote_image", side_effect=self._stored).start()
        self.uploaded = mock.patch.object(cld, "upload_image_bytes", side_effect=self._uploaded).start()
        self.last_bytes = None

    def _meta(self, public_id, size):
        return {
            "public_id": public_id,
            "secure_url": f"https://res.cloudinary.com/flashx-test/image/upload/{public_id}.gif",
            "bytes": size,
            "width": 320,
            "height": 320,
            "pages": 12,
        }

    def _stored(self, url, user_id, purpose):
        self.last_url = url
        return self._meta(f"flashx/u{user_id}/gif/fromvideo", 50_000)

    def _uploaded(self, data, user_id, purpose, filename="x.gif"):
        self.last_bytes = data
        return self._meta(f"flashx/u{user_id}/gif/fromimages", len(data))

    def test_gif_from_video_segment(self):
        video = self.make_asset(self.me, purpose="gif", resource_type="video")
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                reverse("gifs-from-video"),
                {
                    "media_id": video.pk,
                    "start": 2,
                    "end": 5.5,
                    "width": 320,
                    "fps": 12,
                    "speed": 2,
                    "text": "When it works",
                },
                format="json",
            )
        data = self.assertOk(res, 201)
        self.assertEqual((data["source"], data["is_owner"], data["is_public"]), ("video", True, False))
        for part in ("so_2.00", "eo_5.50", "w_320", "fps_12", "e_accelerate:100", ".gif"):
            self.assertIn(part, self.last_url)
        self.assertIn("When", self.last_url)
        # The source video isn't kept.
        self.assertFalse(MediaAsset.objects.filter(pk=video.pk).exists())
        self.destroy.assert_any_call(video.public_id, "video")

    def test_video_rules(self):
        video = self.make_asset(self.me, purpose="gif", resource_type="video")  # 10 s long
        self.assertError(
            self.client.post(reverse("gifs-from-video"), {"media_id": video.pk, "start": 4, "end": 3}, format="json"),
            400,
            "validation_error",
        )
        self.assertError(
            self.client.post(
                reverse("gifs-from-video"), {"media_id": video.pk, "start": 0, "end": 4, "width": 1080}, format="json"
            ),
            400,
            "validation_error",
        )
        photo = self.make_asset(self.me, purpose="gif")
        self.assertError(
            self.client.post(reverse("gifs-from-video"), {"media_id": photo.pk, "start": 0, "end": 2}, format="json"),
            400,
            "unsupported_file_type",
        )

    def test_gif_from_photos_keeps_order_and_looping(self):
        photos = [self.make_asset(self.me, purpose="gif") for _ in range(3)]
        colors = {p.public_id: c for p, c in zip(photos, [(255, 0, 0), (0, 255, 0), (0, 0, 255)], strict=True)}

        def fetch(url):
            pid = next(k for k in colors if k in url)
            self.assertIn("c_pad", url)
            return png(colors[pid])

        order = [photos[2].pk, photos[0].pk, photos[1].pk]
        with mock.patch.object(gifs, "_fetch", side_effect=fetch), self.captureOnCommitCallbacks(execute=True):
            data = self.assertOk(
                self.client.post(
                    reverse("gifs-from-images"),
                    {"media_ids": order, "delay_ms": 300, "loop": False, "title": "RGB"},
                    format="json",
                ),
                201,
            )
        self.assertEqual((data["source"], data["title"]), ("images", "RGB"))
        im = Image.open(io.BytesIO(self.last_bytes))
        self.assertEqual(im.n_frames, 3)
        self.assertNotIn("loop", im.info)  # plays once
        firsts = []
        for i in range(3):
            im.seek(i)
            firsts.append(im.convert("RGB").getpixel((5, 5)))
        self.assertEqual([max(range(3), key=lambda k: c[k]) for c in firsts], [2, 0, 1])
        self.assertEqual(im.info.get("duration"), 300)
        self.assertFalse(MediaAsset.objects.filter(pk__in=order).exists())

    def test_photo_rules(self):
        one = self.make_asset(self.me, purpose="gif")
        self.assertError(self.client.post(reverse("gifs-from-images"), {"media_ids": [one.pk]}, format="json"), 400)
        other_user_photo = self.make_asset(self.make_user(), purpose="gif")
        self.assertError(
            self.client.post(reverse("gifs-from-images"), {"media_ids": [one.pk, other_user_photo.pk]}, format="json"),
            400,
            "media_unavailable",
        )

    def test_upload_gif_file_and_library_privacy(self):
        gif_file = self.make_asset(self.me, purpose="gif", format="gif")
        mine = self.assertOk(
            self.client.post(reverse("gifs-upload"), {"media_id": gif_file.pk, "title": "dance"}, format="json"), 201
        )
        jpg = self.make_asset(self.me, purpose="gif", format="jpg")
        self.assertError(
            self.client.post(reverse("gifs-upload"), {"media_id": jpg.pk}, format="json"), 400, "unsupported_file_type"
        )
        self.assertEqual(
            [g["id"] for g in self.assertOk(self.client.get(reverse("gifs-mine")))["results"]], [mine["id"]]
        )
        other = self.auth(self.make_user("viewer"))
        self.assertError(self.client.get(reverse("gifs-detail", args=[mine["id"]])), 404)
        self.assertEqual(self.assertOk(self.client.get(reverse("gifs-discover"), {"q": "dance"}))["results"], [])
        self.assertError(
            self.client.patch(reverse("gifs-detail", args=[mine["id"]]), {"is_public": True}, format="json"), 404
        )
        self.auth(self.me)
        self.assertOk(self.client.patch(reverse("gifs-detail", args=[mine["id"]]), {"is_public": True}, format="json"))
        self.auth(other)
        found = self.assertOk(self.client.get(reverse("gifs-discover"), {"q": "dance"}))["results"]
        self.assertEqual([g["id"] for g in found], [mine["id"]])
        self.assertError(self.client.delete(reverse("gifs-detail", args=[mine["id"]])), 403, "permission_denied")
        self.auth(self.me)
        self.assertOk(self.client.delete(reverse("gifs-detail", args=[mine["id"]])), 204)
        self.assertFalse(Gif.objects.exists())

    def test_library_gif_in_direct_message(self):
        gif = Gif.objects.create(
            owner=self.me,
            source="upload",
            public_id="flashx/u/gif/z",
            url="https://res.cloudinary.com/x/image/upload/z.gif",
        )
        friend = self.make_user("friend")
        send = lambda url: self.client.post(  # noqa: E731
            reverse("messages-conversations"), {"recipient_id": friend.pk, "gif_url": url}, format="json"
        )
        sent = self.assertOk(send(gif.url), 201)
        self.assertIn(gif.url, str(sent))
        self.auth(friend)
        other = Gif.objects.create(
            owner=self.me,
            source="upload",
            public_id="flashx/u/gif/y",
            url="https://res.cloudinary.com/x/image/upload/y.gif",
        )
        friend_send = self.client.post(
            reverse("messages-conversations"), {"recipient_id": self.me.pk, "gif_url": other.url}, format="json"
        )
        self.assertError(friend_send, 400, "invalid_gif")
