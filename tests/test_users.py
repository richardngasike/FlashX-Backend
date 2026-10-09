from django.urls import reverse

from apps.notifications.models import Notification

from .base import PASSWORD, FlashXTestCase


class ProfileTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("richyict"))

    def test_me_and_update(self):
        data = self.assertOk(
            self.client.patch(
                reverse("users-me"),
                {
                    "full_name": "Richard Ngasike",
                    "bio": "Mobile App Developer",
                    "website": "https://richardngasike.co.ke",
                },
            )
        )
        self.assertEqual(data["full_name"], "Richard Ngasike")
        self.assertEqual(data["website"], "https://richardngasike.co.ke")
        self.assertTrue(data["is_me"])

    def test_username_change_validates_uniqueness(self):
        self.make_user("taken")
        self.assertError(self.client.patch(reverse("users-me"), {"username": "TAKEN"}), 400)
        self.assertEqual(
            self.assertOk(self.client.patch(reverse("users-me"), {"username": "richy.new"}))["username"], "richy.new"
        )

    def test_profile_and_cover_images_replace_and_release_old(self):
        a1 = self.make_asset(self.me, "avatar")
        data = self.assertOk(self.client.patch(reverse("users-me"), {"profile_image_id": a1.id}))
        self.assertIn("res.cloudinary.com", data["avatar"]["url"])
        a2 = self.make_asset(self.me, "avatar")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.patch(reverse("users-me"), {"profile_image_id": a2.id}))
        self.destroy.assert_called_with(a1.public_id, "image")
        cover = self.make_asset(self.me, "cover")
        self.assertIsNotNone(
            self.assertOk(self.client.patch(reverse("users-me"), {"cover_image_id": cover.id}))["cover"]
        )

    def test_cannot_use_someone_elses_or_wrong_purpose_asset(self):
        other = self.make_user()
        self.assertError(
            self.client.patch(reverse("users-me"), {"profile_image_id": self.make_asset(other, "avatar").id}), 400
        )
        self.assertError(
            self.client.patch(reverse("users-me"), {"profile_image_id": self.make_asset(self.me, "post").id}), 400
        )

    def test_lookup_by_id_and_username(self):
        other = self.make_user("kelvin.m")
        self.assertEqual(
            self.assertOk(self.client.get(reverse("users-detail", args=[other.id])))["username"], "kelvin.m"
        )
        self.assertEqual(
            self.assertOk(self.client.get(reverse("users-by-username", args=["KELVIN.M"])))["id"], other.id
        )
        other.is_active = False
        other.save()
        self.assertError(self.client.get(reverse("users-detail", args=[other.id])), 404)

    def test_delete_account_requires_password_and_fixes_counts(self):
        friend = self.make_user()
        self.client.post(reverse("users-follow", args=[friend.id]))
        self.assertError(self.client.delete(reverse("users-me"), {"password": "nope"}), 400)
        self.assertOk(self.client.delete(reverse("users-me"), {"password": PASSWORD}), 204)
        friend.refresh_from_db()
        self.assertEqual(friend.followers_count, 0)


class FollowTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user())
        self.other = self.make_user()

    def test_follow_unfollow_counts_and_notification(self):
        data = self.assertOk(self.client.post(reverse("users-follow", args=[self.other.id])), 201)
        self.assertEqual(data["followers_count"], 1)
        # Idempotent
        self.assertOk(self.client.post(reverse("users-follow", args=[self.other.id])), 200)
        self.me.refresh_from_db()
        self.assertEqual(self.me.following_count, 1)
        self.assertTrue(Notification.objects.filter(recipient=self.other, notification_type="follow").exists())
        profile = self.assertOk(self.client.get(reverse("users-detail", args=[self.other.id])))
        self.assertTrue(profile["is_following"])

        self.assertEqual(
            self.assertOk(self.client.delete(reverse("users-follow", args=[self.other.id])))["followers_count"], 0
        )
        self.assertFalse(Notification.objects.filter(recipient=self.other, notification_type="follow").exists())

    def test_cannot_follow_self(self):
        self.assertError(self.client.post(reverse("users-follow", args=[self.me.id])), 400, "self_follow")

    def test_followers_following_lists_and_remove_follower(self):
        fan = self.make_user("fan_one")
        from apps.follows.services import follow

        follow(fan, self.me)
        follow(self.me, self.other)
        followers = self.assertOk(self.client.get(reverse("users-followers", args=[self.me.id])))["results"]
        self.assertEqual([u["username"] for u in followers], ["fan_one"])
        self.assertTrue(followers[0]["follows_you"])
        following = self.assertOk(self.client.get(reverse("users-following", args=[self.me.id])))["results"]
        self.assertEqual(following[0]["id"], self.other.id)
        self.assertOk(self.client.delete(reverse("users-remove-follower", args=[fan.id])), 204)
        self.assertEqual(self.assertOk(self.client.get(reverse("users-followers", args=[self.me.id])))["count"], 0)

    def test_suggestions_rank_mutuals_and_exclude_followed(self):
        from apps.follows.services import follow

        mutual = self.make_user("mutual")
        follow(self.me, self.other)
        follow(self.other, mutual)
        results = self.assertOk(self.client.get(reverse("users-suggested")))["results"]
        ids = [u["id"] for u in results]
        self.assertEqual(ids[0], mutual.id)
        self.assertNotIn(self.other.id, ids)
        self.assertNotIn(self.me.id, ids)
