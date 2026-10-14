from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.music import services as music
from apps.music.models import SavedSound, Sound
from apps.reports.models import Report

from .base import FlashXTestCase

NO_CATALOGUE = {"JAMENDO_CLIENT_ID": "", "OPENVERSE_ENABLED": False}


@override_settings(MUSIC=NO_CATALOGUE)
class SoundLibraryTests(FlashXTestCase):
    """Uploaded sounds, saving, recent and trending sounds, owner controls and reports."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.owner = self.auth(self.make_user("beatmaker"))

    def audio(self, fmt="m4a"):
        from apps.media.models import MediaAsset

        asset = self.make_asset(self.owner, purpose="sound", resource_type="video", format=fmt)
        MediaAsset.objects.filter(pk=asset.pk).update(width=None, height=None)
        asset.refresh_from_db()
        return asset

    def upload(self, **body):
        audio = self.audio()
        data = {"media_id": audio.pk, "title": "Nairobi drums", **body}
        return self.client.post(reverse("music-sound-create"), data, format="json")

    def test_upload_publishes_a_trimmed_original_sound(self):
        data = self.assertOk(self.upload(start=2, end=17, description="Recorded at home"), 201)
        self.assertEqual((data["title"], data["origin"], data["duration"]), ("Nairobi drums", "upload", 8.0))
        self.assertTrue(data["is_owner"] and data["usable"] and data["is_public"])
        self.assertIn("so_2.00", data["audio_url"])
        self.assertIn("eo_10.00", data["audio_url"])  # the stored file is 10 s long: the end is clipped to it
        sound = Sound.objects.get(pk=data["id"])
        self.assertEqual(sound.owner, self.owner)
        self.assertTrue(sound.asset.is_attached)

    def test_upload_validation(self):
        self.assertError(self.upload(title="  "), 400, "validation_error")
        self.assertError(self.upload(start=20), 400, "validation_error")
        photo = self.make_asset(self.owner, purpose="post")
        self.assertError(
            self.client.post(reverse("music-sound-create"), {"media_id": photo.pk, "title": "x"}, format="json"),
            400,
            "media_purpose_mismatch",
        )

    def test_others_use_public_sound_on_a_reel_and_owner_can_switch_reuse_off(self):
        sound_id = self.assertOk(self.upload(), 201)["id"]
        fan = self.auth(self.make_user("fan"))
        found = self.assertOk(self.client.get(reverse("music-search"), {"q": "drums"}))["original"]
        self.assertEqual([s["id"] for s in found], [sound_id])
        video = self.make_asset(fan, purpose="reel", resource_type="video")
        reel = self.assertOk(self.client.post(reverse("reels-list"), {"media_id": video.pk, "sound_id": sound_id}), 201)
        self.assertEqual(reel["sound"]["owner_username"], "beatmaker")
        self.assertTrue(reel["sound"]["plays_separately"])

        self.auth(self.owner)
        data = self.assertOk(
            self.client.patch(reverse("music-sound", args=[sound_id]), {"allow_reuse": False}, format="json")
        )
        self.assertFalse(data["allow_reuse"])
        self.auth(fan)
        other = self.make_asset(fan, purpose="reel", resource_type="video")
        self.assertError(
            self.client.post(reverse("reels-list"), {"media_id": other.pk, "sound_id": sound_id}),
            400,
            "sound_unavailable",
        )
        # The owner can still use it.
        self.auth(self.owner)
        mine = self.make_asset(self.owner, purpose="reel", resource_type="video")
        self.assertOk(self.client.post(reverse("reels-list"), {"media_id": mine.pk, "sound_id": sound_id}), 201)

    def test_private_sound_hidden_from_others(self):
        sound_id = self.assertOk(self.upload(is_public=False), 201)["id"]
        self.auth(self.make_user())
        self.assertError(self.client.get(reverse("music-sound", args=[sound_id])), 404)
        self.assertEqual(self.assertOk(self.client.get(reverse("music-search"), {"q": "drums"}))["original"], [])

    def test_only_owner_edits_and_deletes(self):
        sound_id = self.assertOk(self.upload(), 201)["id"]
        self.auth(self.make_user())
        self.assertError(
            self.client.patch(reverse("music-sound", args=[sound_id]), {"title": "mine now"}, format="json"),
            403,
            "permission_denied",
        )
        self.assertError(self.client.delete(reverse("music-sound", args=[sound_id])), 403, "permission_denied")
        self.auth(self.owner)
        self.assertOk(self.client.patch(reverse("music-sound", args=[sound_id]), {"title": "Drums v2"}, format="json"))
        self.assertEqual(Sound.objects.get(pk=sound_id).title, "Drums v2")

    def test_deleted_upload_stops_playing_on_posts_that_used_it(self):
        sound_id = self.assertOk(self.upload(), 201)["id"]
        fan = self.auth(self.make_user())
        post = self.assertOk(self.client.post(reverse("posts-list"), {"caption": "vibe", "sound_id": sound_id}), 201)
        self.client.post(reverse("music-sound-save", args=[sound_id]))
        self.auth(self.owner)
        self.assertOk(self.client.delete(reverse("music-sound", args=[sound_id])), 204)
        self.auth(fan)
        shown = self.assertOk(self.client.get(reverse("posts-detail", args=[post["id"]])))["sound"]
        # Same name for attribution, no audio, never a different song.
        self.assertEqual((shown["title"], shown["audio_url"], shown["available"]), ("Nairobi drums", "", False))
        self.assertFalse(SavedSound.objects.exists())
        self.assertError(
            self.client.post(reverse("posts-list"), {"caption": "again", "sound_id": sound_id}),
            400,
            "sound_unavailable",
        )
        self.assertEqual(self.assertOk(self.client.get(reverse("music-mine")))["results"], [])

    def test_deleting_a_video_sound_only_stops_reuse(self):
        video = self.make_asset(self.owner, purpose="post", resource_type="video")
        post = self.assertOk(self.client.post(reverse("posts-list"), {"caption": "x", "media_ids": [video.pk]}), 201)
        sound_id = post["sound"]["id"]
        self.assertEqual(post["sound"]["origin"], "video")
        self.assertOk(self.client.delete(reverse("music-sound", args=[sound_id])), 204)
        again = self.assertOk(self.client.get(reverse("posts-detail", args=[post["id"]])))["sound"]
        self.assertTrue(again["available"])
        self.assertFalse(again["allow_reuse"])
        from apps.posts.models import Post

        self.assertFalse(Post.objects.get(pk=post["id"]).allow_sound_reuse)

    def test_save_unsave_and_saved_list(self):
        sound_id = self.assertOk(self.upload(), 201)["id"]
        fan = self.auth(self.make_user())
        self.assertEqual(
            self.assertOk(self.client.post(reverse("music-sound-save", args=[sound_id]))), {"is_saved": True}
        )
        self.assertOk(self.client.post(reverse("music-sound-save", args=[sound_id])))  # idempotent
        self.assertEqual(SavedSound.objects.filter(user=fan).count(), 1)
        saved = self.assertOk(self.client.get(reverse("music-saved")))["results"]
        self.assertEqual([(s["id"], s["is_saved"]) for s in saved], [(sound_id, True)])
        self.assertTrue(self.assertOk(self.client.get(reverse("music-sound", args=[sound_id])))["is_saved"])
        self.assertOk(self.client.delete(reverse("music-sound-save", args=[sound_id])))
        self.assertEqual(self.assertOk(self.client.get(reverse("music-saved")))["results"], [])

    def test_recent_and_trending(self):
        first = self.assertOk(self.upload(), 201)["id"]
        audio2 = self.audio("mp3")
        second = self.assertOk(
            self.client.post(reverse("music-sound-create"), {"media_id": audio2.pk, "title": "Bass"}, format="json"),
            201,
        )["id"]
        fan = self.auth(self.make_user("fan2"))
        self.assertOk(self.client.post(reverse("posts-list"), {"caption": "a", "sound_id": first}), 201)
        self.assertOk(self.client.post(reverse("posts-list"), {"caption": "b", "sound_id": second}), 201)
        self.assertOk(self.client.post(reverse("posts-list"), {"caption": "c", "sound_id": second}), 201)
        recent = self.assertOk(self.client.get(reverse("music-recent")))["results"]
        self.assertEqual([s["id"] for s in recent], [second, first])
        trending = self.assertOk(self.client.get(reverse("music-trending")))["results"]
        self.assertEqual([s["id"] for s in trending], [second, first])
        browse = self.assertOk(self.client.get(reverse("music-search")))
        self.assertEqual([s["id"] for s in browse["trending"]], [second, first])
        self.assertIsNotNone(fan)

    def test_duration_filter(self):
        self.assertOk(self.upload(start=0, end=5), 201)
        self.assertEqual(
            self.assertOk(self.client.get(reverse("music-search"), {"q": "drums", "max_duration": 3}))["original"], []
        )
        self.assertEqual(
            len(
                self.assertOk(self.client.get(reverse("music-search"), {"q": "drums", "max_duration": 30}))["original"]
            ),
            1,
        )

    def test_report_sound_and_takedown(self):
        sound_id = self.assertOk(self.upload(), 201)["id"]
        self.assertError(
            self.client.post(reverse("music-sound-report", args=[sound_id]), {"reason": "ip"}, format="json"),
            400,
            "self_report",
        )
        reporter = self.auth(self.make_user())
        self.assertOk(
            self.client.post(
                reverse("music-sound-report", args=[sound_id]), {"reason": "ip", "details": "my song"}, format="json"
            ),
            201,
        )
        report = Report.objects.get(sound_id=sound_id)
        self.assertEqual((report.target_type, report.reporter), ("sound", reporter))
        from apps.reports.services import resolve

        resolve([report.pk], self.make_user(is_staff=True), status=Report.Status.ACTIONED, hide_content=True)
        sound = Sound.objects.get(pk=sound_id)
        self.assertIsNotNone(sound.removed_at)
        self.assertEqual(music.sound_payload(sound)["audio_url"], "")
