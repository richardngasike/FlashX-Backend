from django.urls import reverse

from apps.blocks.models import Block
from apps.follows.models import Follow
from apps.follows.services import follow
from apps.messaging.services import get_or_create_direct
from apps.notifications.models import Notification
from apps.reels.services import create_reel
from apps.stories.services import create_story

from .base import FlashXTestCase


class BlockTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.make_user("me_user")
        self.other = self.make_user("other_user")
        self.auth(self.me)

    def block(self, user=None):
        return self.client.post(reverse("users-block", args=[(user or self.other).pk]))

    def test_block_unblock_is_idempotent_and_listed(self):
        self.assertEqual(self.assertOk(self.block(), 201), {"is_blocked": True})
        self.assertOk(self.block(), 200)
        rows = self.assertOk(self.client.get(reverse("users-me-blocked")))["results"]
        self.assertEqual([r["username"] for r in rows], ["other_user"])
        self.assertIn("blocked_at", rows[0])
        self.assertEqual(
            self.assertOk(self.client.delete(reverse("users-block", args=[self.other.pk]))), {"is_blocked": False}
        )
        self.assertFalse(Block.objects.exists())

    def test_cannot_block_self(self):
        self.assertError(self.client.post(reverse("users-block", args=[self.me.pk])), 400, "self_block")

    def test_block_removes_follows_both_ways_and_shared_notifications(self):
        follow(self.me, self.other)
        follow(self.other, self.me)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertOk(self.block(), 201)
        self.assertFalse(Follow.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.me.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual((self.me.followers_count, self.me.following_count, self.other.followers_count), (0, 0, 0))

    def test_follow_is_refused_either_direction(self):
        self.block()
        self.assertError(self.client.post(reverse("users-follow", args=[self.other.pk])), 403, "blocked")
        self.auth(self.other)
        self.assertError(self.client.post(reverse("users-follow", args=[self.me.pk])), 403, "blocked")

    def test_profile_visibility(self):
        self.block()
        profile = self.assertOk(self.client.get(reverse("users-detail", args=[self.other.pk])))
        self.assertTrue(profile["is_blocked"])
        self.auth(self.other)
        self.assertError(self.client.get(reverse("users-detail", args=[self.me.pk])), 404)
        self.assertError(self.client.get(reverse("users-by-username", args=["me_user"])), 404)

    def test_content_hidden_both_ways(self):
        their_post = self.make_post(self.other, caption="blocked caption")
        my_post = self.make_post(self.me, caption="my caption")
        reel = create_reel(self.other, media_id=self.make_asset(self.other, "reel", "video").pk)
        create_story(self.other, media_id=self.make_asset(self.other, "story").pk)
        self.block()

        posts = self.assertOk(self.client.get(reverse("posts-list")))["results"]
        self.assertNotIn(their_post.pk, [p["id"] for p in posts])
        self.assertError(self.client.get(reverse("posts-detail", args=[their_post.pk])), 404)
        self.assertError(self.client.get(reverse("reels-detail", args=[reel.pk])), 404)
        self.assertError(self.client.get(f"/api/stories/user/{self.other.pk}/"), 404)
        search = self.assertOk(self.client.get(reverse("search"), {"q": "other_user", "type": "users"}))
        self.assertEqual(search["results"], [])

        self.auth(self.other)
        self.assertError(self.client.get(reverse("posts-detail", args=[my_post.pk])), 404)
        suggested = self.assertOk(self.client.get(reverse("users-suggested")))["results"]
        self.assertNotIn(self.me.pk, [u["id"] for u in suggested])

    def test_comments_from_blocked_users_are_hidden(self):
        from apps.comments.services import create_comment

        post = self.make_post(self.me)
        create_comment(self.other, post, "hidden comment")
        create_comment(self.make_user(), post, "visible comment")
        self.block()
        rows = self.assertOk(self.client.get(reverse("posts-comments", args=[post.pk])))["results"]
        self.assertEqual([r["content"] for r in rows], ["visible comment"])

    def test_messaging_is_refused_but_thread_shows_blocked(self):
        convo = get_or_create_direct(self.me, self.other)
        self.block()
        self.assertError(
            self.client.post(reverse("messages-conversations"), {"recipient_id": self.other.pk, "content": "hi"}),
            403,
            "blocked",
        )
        self.assertError(
            self.client.post(reverse("messages-conversations"), {"conversation_id": str(convo.pk), "content": "hi"}),
            403,
            "blocked",
        )
        info = self.assertOk(self.client.get(reverse("messages-info", args=[convo.pk])))
        self.assertTrue(info["is_blocked"])
        self.auth(self.other)
        self.assertError(
            self.client.post(reverse("messages-conversations"), {"conversation_id": str(convo.pk), "content": "hi"}),
            403,
            "blocked",
        )

    def test_no_notifications_between_blocked_users(self):
        post = self.make_post(self.me)
        self.block()
        from apps.likes.services import like

        like(self.other, post)
        self.assertFalse(Notification.objects.exists())

    def test_unblock_restores_visibility(self):
        post = self.make_post(self.other)
        self.block()
        self.client.delete(reverse("users-block", args=[self.other.pk]))
        self.assertOk(self.client.get(reverse("posts-detail", args=[post.pk])))
