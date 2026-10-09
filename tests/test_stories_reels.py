from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.messaging.models import Message
from apps.notifications.models import Notification
from apps.reels.models import Reel
from apps.stories.models import Story
from apps.stories.services import create_story, purge_expired

from .base import FlashXTestCase


class StoryTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("me"))
        self.friend = self.make_user("friend")
        from apps.follows.services import follow

        follow(self.me, self.friend)

    def test_create_and_tray(self):
        a = self.make_asset(self.me, "story")
        data = self.assertOk(self.client.post(reverse("stories-list"), {"media_id": a.id, "caption": "hi"}), 201)
        self.assertEqual(data["media"]["type"], "image")
        create_story(self.friend, media_id=self.make_asset(self.friend, "story", "video").id)
        tray = self.assertOk(self.client.get(reverse("stories-list")))["results"]
        self.assertEqual([g["user"]["username"] for g in tray], ["me", "friend"])
        self.assertTrue(tray[1]["has_unseen"])

    def test_post_media_cannot_be_used_as_story(self):
        self.assertError(
            self.client.post(reverse("stories-list"), {"media_id": self.make_asset(self.me, "post").id}), 400
        )

    def test_view_react_reply_and_viewers(self):
        story = create_story(self.friend, media_id=self.make_asset(self.friend, "story").id)
        self.assertOk(self.client.post(reverse("stories-view", args=[story.id])))
        self.assertOk(self.client.post(reverse("stories-view", args=[story.id])))
        self.assertEqual(Story.objects.get(pk=story.pk).views_count, 1)
        tray = self.assertOk(self.client.get(reverse("stories-list")))["results"]
        self.assertFalse([g for g in tray if g["user"]["username"] == "friend"][0]["has_unseen"])

        self.assertOk(self.client.post(reverse("stories-react", args=[story.id]), {"reaction": "fire"}))
        self.assertTrue(Notification.objects.filter(recipient=self.friend, notification_type="story_reaction").exists())
        self.assertError(self.client.post(reverse("stories-react", args=[story.id]), {"reaction": "nope"}), 400)

        reply = self.assertOk(self.client.post(reverse("stories-reply", args=[story.id]), {"content": "wow"}), 201)
        msg = Message.objects.get(pk=reply["message_id"])
        self.assertEqual(msg.story_id, story.id)

        # Only the author may list viewers
        self.assertError(self.client.get(reverse("stories-viewers", args=[story.id])), 403)
        self.auth(self.friend)
        viewers = self.assertOk(self.client.get(reverse("stories-viewers", args=[story.id])))["results"]
        self.assertEqual(viewers[0]["username"], "me")
        self.assertEqual(viewers[0]["reaction"], "fire")

    def test_expired_stories_hidden_and_purged(self):
        story = create_story(self.friend, media_id=self.make_asset(self.friend, "story").id)
        Story.objects.filter(pk=story.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.assertOk(self.client.get(reverse("stories-list")))["results"], [])
        self.assertError(self.client.get(reverse("stories-detail", args=[story.id])), 404)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(purge_expired(), 1)
        self.destroy.assert_called_once()

    def test_only_author_deletes(self):
        story = create_story(self.friend, media_id=self.make_asset(self.friend, "story").id)
        self.assertError(self.client.delete(reverse("stories-detail", args=[story.id])), 403)
        self.auth(self.friend)
        self.assertOk(self.client.delete(reverse("stories-detail", args=[story.id])), 204)


class ReelTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("skater_ke"))

    def create(self, caption="Small steps. #skate"):
        a = self.make_asset(self.me, "reel", "video")
        return self.client.post(reverse("reels-list"), {"media_id": a.id, "caption": caption})

    def test_create_list_and_view_counting(self):
        reel = self.assertOk(self.create(), 201)
        self.assertIn("poster", reel["video"])
        self.assertEqual(reel["hashtags"], ["skate"])
        viewer = self.make_user()
        self.auth(viewer)
        self.assertEqual(
            self.assertOk(self.client.post(reverse("reels-view", args=[reel["id"]]), {"watched_seconds": 3}))["views"],
            1,
        )
        self.assertEqual(
            self.assertOk(self.client.post(reverse("reels-view", args=[reel["id"]]), {"watched_seconds": 9}))["views"],
            1,
        )
        listing = self.assertOk(self.client.get(reverse("reels-list"), {"page_size": 5}))
        self.assertEqual(listing["results"][0]["id"], reel["id"])
        self.assertEqual(self.assertOk(self.client.get(reverse("reels-list"), {"feed": "following"}))["results"], [])

    def test_image_asset_rejected_for_reel(self):
        a = self.make_asset(self.me, "post", "image")
        self.assertError(self.client.post(reverse("reels-list"), {"media_id": a.id}), 400)

    def test_engagement_and_delete(self):
        reel = self.assertOk(self.create(), 201)
        fan = self.make_user()
        self.auth(fan)
        self.assertEqual(self.assertOk(self.client.post(reverse("reels-like", args=[reel["id"]])))["likes_count"], 1)
        self.assertOk(self.client.post(reverse("reels-save", args=[reel["id"]])))
        self.assertOk(self.client.post(reverse("reels-comments", args=[reel["id"]]), {"content": "sick"}), 201)
        self.assertEqual(
            self.assertOk(self.client.get(reverse("users-me-saved"), {"type": "reels"}))["results"][0]["id"], reel["id"]
        )
        self.assertError(self.client.delete(reverse("reels-detail", args=[reel["id"]])), 403)
        self.auth(self.me)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.delete(reverse("reels-detail", args=[reel["id"]])), 204)
        self.assertFalse(Reel.objects.exists())
        self.destroy.assert_called_once()
        self.me.refresh_from_db()
        self.assertEqual(self.me.reels_count, 0)
