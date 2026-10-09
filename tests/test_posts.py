from django.urls import reverse

from apps.notifications.models import Notification
from apps.posts.models import Hashtag, Post
from apps.users.models import User

from .base import FlashXTestCase


class PostCrudTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("samburu_traveller"))

    def test_create_text_post(self):
        data = self.assertOk(
            self.client.post(reverse("posts-list"), {"caption": "The beauty of home. #Samburu #kenya"}), 201
        )
        self.assertEqual(data["type"], "text")
        self.assertEqual(sorted(data["hashtags"]), ["kenya", "samburu"])
        self.assertTrue(data["is_owner"])
        self.assertEqual(User.objects.get(pk=self.me.pk).posts_count, 1)
        self.assertEqual(Hashtag.objects.get(name="samburu").posts_count, 1)

    def test_create_media_post_preserves_order(self):
        a, b, v = self.make_asset(self.me), self.make_asset(self.me), self.make_asset(self.me, resource_type="video")
        data = self.assertOk(
            self.client.post(reverse("posts-list"), {"caption": "", "media_ids": [v.id, a.id, b.id]}), 201
        )
        self.assertEqual([m["media_type"] for m in data["media"]], ["video", "image", "image"])
        self.assertEqual(data["type"], "mixed")
        self.assertIn("poster", data["media"][0]["urls"])
        a.refresh_from_db()
        self.assertTrue(a.is_attached)
        # Assets cannot be reused.
        self.assertError(self.client.post(reverse("posts-list"), {"media_ids": [a.id]}), 400, "media_unavailable")

    def test_empty_post_rejected(self):
        self.assertError(self.client.post(reverse("posts-list"), {"caption": "   "}), 400)

    def test_cannot_attach_others_media(self):
        theirs = self.make_asset(self.make_user())
        self.assertError(self.client.post(reverse("posts-list"), {"media_ids": [theirs.id]}), 400, "media_unavailable")

    def test_update_caption_resyncs_hashtags(self):
        post = self.make_post(self.me, "#one #two")
        data = self.assertOk(self.client.patch(reverse("posts-detail", args=[post.id]), {"caption": "#two #three"}))
        self.assertEqual(sorted(data["hashtags"]), ["three", "two"])
        self.assertEqual(Hashtag.objects.get(name="one").posts_count, 0)
        self.assertError(self.client.patch(reverse("posts-detail", args=[post.id]), {"media_ids": [1]}), 400)

    def test_only_author_can_edit_or_delete(self):
        post = self.make_post(self.make_user())
        self.assertError(self.client.patch(reverse("posts-detail", args=[post.id]), {"caption": "hijack"}), 403)
        self.assertError(self.client.delete(reverse("posts-detail", args=[post.id])), 403)
        self.assertTrue(Post.objects.filter(pk=post.pk).exists())

    def test_delete_post_cleans_up_media_counters_and_notifications(self):
        asset = self.make_asset(self.me)
        post = self.make_post(self.me, "#gone", media_ids=[asset.id])
        fan = self.make_user()
        from apps.likes.services import like

        like(fan, post)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.delete(reverse("posts-detail", args=[post.id])), 204)
        self.destroy.assert_called_once_with(asset.public_id, "image")
        self.assertEqual(User.objects.get(pk=self.me.pk).posts_count, 0)
        self.assertEqual(Hashtag.objects.get(name="gone").posts_count, 0)
        self.assertFalse(Notification.objects.filter(target_type="post", reference_id=str(post.id)).exists())

    def test_tags_and_mentions_notify(self):
        tagged, mentioned = self.make_user("tagged_one"), self.make_user("mention_me")
        self.client.post(reverse("posts-list"), {"caption": "hi @mention_me", "tagged_user_ids": [tagged.id]})
        self.assertTrue(Notification.objects.filter(recipient=tagged, notification_type="tag").exists())
        self.assertTrue(Notification.objects.filter(recipient=mentioned, notification_type="mention").exists())


class VisibilityAndFeedTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user())
        self.friend = self.make_user()
        self.stranger = self.make_user()
        from apps.follows.services import follow

        follow(self.me, self.friend)

    def test_visibility_rules(self):
        followers_only = self.make_post(self.friend, "friends", visibility="followers")
        private = self.make_post(self.friend, "secret", visibility="private")
        stranger_fo = self.make_post(self.stranger, "not for you", visibility="followers")
        self.assertOk(self.client.get(reverse("posts-detail", args=[followers_only.id])))
        self.assertError(self.client.get(reverse("posts-detail", args=[private.id])), 404)
        self.assertError(self.client.get(reverse("posts-detail", args=[stranger_fo.id])), 404)
        # Can't like what you can't see.
        self.assertError(self.client.post(reverse("posts-like", args=[private.id])), 404)

    def test_hidden_posts_and_inactive_authors_disappear(self):
        hidden = self.make_post(self.friend, "bad")
        Post.objects.filter(pk=hidden.pk).update(is_hidden=True)
        visible = self.make_post(self.friend, "good")
        ids = [p["id"] for p in self.assertOk(self.client.get(reverse("posts-feed")))["results"]]
        self.assertEqual(ids, [visible.id])
        User.objects.filter(pk=self.friend.pk).update(is_active=False)
        self.assertEqual(self.assertOk(self.client.get(reverse("posts-feed")))["results"], [])

    def test_feed_contains_followed_and_own_posts_newest_first_with_cursor(self):
        own = self.make_post(self.me, "mine")
        friend_posts = [self.make_post(self.friend, f"p{i}") for i in range(4)]
        self.make_post(self.stranger, "nope")
        page1 = self.assertOk(self.client.get(reverse("posts-feed"), {"page_size": 3}))
        self.assertEqual([p["id"] for p in page1["results"]], [p.id for p in friend_posts[:0:-1]])
        self.assertTrue(page1["results"][0]["author"]["is_following"])
        page2 = self.assertOk(self.client.get(page1["next"]))
        self.assertEqual([p["id"] for p in page2["results"]], [friend_posts[0].id, own.id])
        self.assertIsNone(page2["next"])

    def test_list_filters(self):
        p = self.make_post(self.friend, "#nairobi sunsets")
        self.make_post(self.friend, "other")
        self.assertEqual(
            [
                x["id"]
                for x in self.assertOk(self.client.get(reverse("posts-list"), {"hashtag": "#Nairobi"}))["results"]
            ],
            [p.id],
        )
        self.assertEqual(
            len(self.assertOk(self.client.get(reverse("posts-list"), {"author": self.friend.id}))["results"]), 2
        )
        self.assertEqual(len(self.assertOk(self.client.get(reverse("posts-list"), {"media": "only"}))["results"]), 0)

    def test_query_count_is_bounded(self):
        for i in range(5):
            self.make_post(self.friend, f"#t{i}", media_ids=[self.make_asset(self.friend).id])
        # Constant regardless of page size: posts (+flags), media, tagged users, hashtags.
        with self.assertNumQueries(4):
            self.client.get(reverse("posts-feed"))


class EngagementTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.author = self.make_user("author")
        self.post = self.make_post(self.author, "Engage with me")
        self.me = self.auth(self.make_user("fan"))

    def test_like_is_idempotent_and_notifies(self):
        self.assertEqual(self.assertOk(self.client.post(reverse("posts-like", args=[self.post.id])))["likes_count"], 1)
        self.assertEqual(self.assertOk(self.client.post(reverse("posts-like", args=[self.post.id])))["likes_count"], 1)
        self.assertEqual(Notification.objects.filter(recipient=self.author, notification_type="like").count(), 1)
        data = self.assertOk(self.client.get(reverse("posts-detail", args=[self.post.id])))
        self.assertTrue(data["is_liked"])
        likers = self.assertOk(self.client.get(reverse("posts-likers", args=[self.post.id])))["results"]
        self.assertEqual(likers[0]["username"], "fan")
        self.assertEqual(
            self.assertOk(self.client.delete(reverse("posts-like", args=[self.post.id])))["likes_count"], 0
        )
        self.assertFalse(Notification.objects.filter(recipient=self.author, notification_type="like").exists())

    def test_save_and_saved_list(self):
        self.assertTrue(self.assertOk(self.client.post(reverse("posts-save", args=[self.post.id])))["is_saved"])
        saved = self.assertOk(self.client.get(reverse("users-me-saved")))["results"]
        self.assertEqual(saved[0]["id"], self.post.id)
        self.assertEqual(
            self.assertOk(self.client.delete(reverse("posts-save", args=[self.post.id])))["saves_count"], 0
        )
        self.assertEqual(self.assertOk(self.client.get(reverse("users-me-saved")))["results"], [])

    def test_share_sends_dm_and_counts(self):
        friend = self.make_user("friend")
        data = self.assertOk(
            self.client.post(
                reverse("posts-share", args=[self.post.id]), {"recipient_ids": [friend.id], "message": "look"}
            ),
            201,
        )
        self.assertEqual(data["shares_count"], 1)
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[data["conversation_ids"][0]])))
        self.assertEqual(thread["results"][0]["shared_post"]["id"], self.post.id)
        self.assertTrue(Notification.objects.filter(recipient=friend, notification_type="share").exists())


class DesignFeatureTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("richyict"))

    def test_mood_event_music_post(self):
        data = self.assertOk(
            self.client.post(
                reverse("posts-list"),
                {
                    "mood": "grateful",
                    "event_title": "Launch party",
                    "event_starts_at": "2026-11-01T18:00:00+03:00",
                    "music_title": "Sauti Sol - Suzanna",
                },
            ),
            201,
        )
        self.assertEqual(data["mood"], "grateful")
        self.assertEqual(data["event"]["title"], "Launch party")
        self.assertEqual(data["music_title"], "Sauti Sol - Suzanna")
        self.assertError(self.client.post(reverse("posts-list"), {"caption": "x", "event_title": "No date"}), 400)
        self.assertError(self.client.post(reverse("posts-list"), {"mood": "angry"}), 400)

    def test_profile_location(self):
        self.assertEqual(
            self.assertOk(self.client.patch(reverse("users-me"), {"location": "Nairobi, Kenya"}))["location"],
            "Nairobi, Kenya",
        )

    def test_liked_posts_and_archive_filter(self):
        other = self.make_user()
        a, b = self.make_post(other, "a"), self.make_post(other, "b")
        self.client.post(reverse("posts-like", args=[b.id]))
        self.client.post(reverse("posts-like", args=[a.id]))
        liked = self.assertOk(self.client.get(reverse("users-me-likes")))["results"]
        self.assertEqual([p["id"] for p in liked], [a.id, b.id])
        private = self.make_post(self.me, "only me", visibility="private")
        self.make_post(self.me, "public one")
        archive = self.assertOk(
            self.client.get(reverse("posts-list"), {"author": self.me.id, "visibility": "private"})
        )["results"]
        self.assertEqual([p["id"] for p in archive], [private.id])

    def test_category_cover_falls_back_to_latest_image(self):
        from apps.posts.models import Category

        cat = Category.objects.create(name="Food", slug="food")
        self.make_post(self.me, "pilau", category=cat, media_ids=[self.make_asset(self.me).id])
        cats = self.assertOk(self.client.get(reverse("explore-categories")))["results"]
        self.assertIn("res.cloudinary.com", cats[0]["cover"])
