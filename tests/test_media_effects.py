from django.urls import reverse

from apps.media import services as media

from .base import FlashXTestCase


class MediaEffectsTests(FlashXTestCase):
    """The editor's look (filter and text overlays) stored with a video and returned with it."""

    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("creator"))

    def test_clean_effects_keeps_only_known_parts(self):
        cleaned = media.clean_effects(
            {
                "filter": "Savanna",
                "strength": 3,
                "unknown": 1,
                "texts": [
                    {"text": "  Hello  ", "style": "neon", "color": "#ff00aa", "x": 2, "y": -1, "size": 9},
                    {"text": "", "style": "bold"},
                    {"text": "Bad style", "style": "comic", "color": "zzz", "align": "middle", "rotation": "x"},
                    "not a dict",
                ],
            }
        )
        self.assertEqual(cleaned["filter"], "savanna")
        self.assertEqual(cleaned["strength"], 1)
        self.assertNotIn("unknown", cleaned)
        first, second = cleaned["texts"]
        self.assertEqual(
            (first["text"], first["style"], first["color"], first["x"], first["y"], first["size"]),
            ("Hello", "neon", "FFFF00AA", 1, 0, 0.4),
        )
        self.assertEqual(
            (second["style"], second["color"], second["align"], second["rotation"]), ("clean", "FFFFFFFF", "center", 0)
        )
        self.assertEqual(media.clean_effects("nope"), {})
        self.assertEqual(media.clean_effects({"filter": "../etc"}), {})
        self.assertEqual(len(media.clean_effects({"texts": [{"text": str(i)} for i in range(30)]})["texts"]), 12)

    def test_reel_returns_its_look(self):
        asset = self.make_asset(self.me, purpose="reel", resource_type="video")
        asset.effects = media.clean_effects({"filter": "dusk", "texts": [{"text": "Hi", "style": "bold"}]})
        asset.save()
        reel = self.assertOk(
            self.client.post(reverse("reels-list"), {"media_id": asset.pk, "caption": "x"}, format="json"), 201
        )
        self.assertEqual(reel["video"]["effects"]["filter"], "dusk")
        listed = self.assertOk(self.client.get(reverse("reels-detail", args=[reel["id"]])))
        self.assertEqual(listed["video"]["effects"]["texts"][0]["text"], "Hi")

    def test_post_video_returns_its_look_and_plain_media_has_none(self):
        video = self.make_asset(self.me, purpose="post", resource_type="video")
        video.effects = {"filter": "noir"}
        video.save()
        photo = self.make_asset(self.me, purpose="post")
        post = self.assertOk(
            self.client.post(reverse("posts-list"), {"caption": "x", "media_ids": [video.pk, photo.pk]}, format="json"),
            201,
        )
        media_rows = self.assertOk(self.client.get(reverse("posts-detail", args=[post["id"]])))["media"]
        self.assertEqual(media_rows[0]["urls"]["effects"], {"filter": "noir"})
        self.assertNotIn("effects", media_rows[1]["urls"])
