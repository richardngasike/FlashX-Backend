from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.posts.models import Category, Post
from apps.reports.models import Report

from .base import FlashXTestCase

User = get_user_model()


class NotificationTests(FlashXTestCase):
    def test_list_unread_and_mark_read(self):
        me = self.make_user("me")
        post = self.make_post(me, "hi")
        fan = self.auth(self.make_user("fan"))
        self.client.post(reverse("posts-like", args=[post.id]))
        self.client.post(reverse("users-follow", args=[me.id]))
        self.auth(me)
        data = self.assertOk(self.client.get(reverse("notifications-list")))["results"]
        self.assertEqual([n["type"] for n in data], ["follow", "like"])
        self.assertEqual(data[1]["target"]["post_id"], post.id)
        self.assertEqual(data[0]["sender"]["id"], fan.id)
        self.assertEqual(self.assertOk(self.client.get(reverse("notifications-unread-count")))["notifications"], 2)
        self.assertOk(self.client.post(reverse("notifications-read", args=[data[0]["id"]])))
        self.assertEqual(self.assertOk(self.client.post(reverse("notifications-read-all")))["marked_read"], 1)
        self.assertError(self.client.post(reverse("notifications-read", args=[999999])), 404)

    def test_cannot_touch_other_users_notifications(self):
        me = self.make_user()
        post = self.make_post(me)
        self.auth(self.make_user())
        self.client.post(reverse("posts-like", args=[post.id]))
        self.assertEqual(self.assertOk(self.client.get(reverse("notifications-list")))["results"], [])


class SearchExploreTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("me"))
        self.nv = self.make_user("nairobi_vibes", full_name="Nairobi Vibes")
        self.make_user("vibes_nairobi")
        self.travel = Category.objects.create(name="Travel", slug="travel")
        self.p = self.make_post(
            self.nv, "Matatu art #nairobi", category=self.travel, media_ids=[self.make_asset(self.nv).id]
        )

    def test_search_all_and_by_type(self):
        data = self.assertOk(self.client.get(reverse("search"), {"q": "nairobi"}))
        self.assertEqual(data["users"][0]["username"], "nairobi_vibes")  # prefix match ranks first
        self.assertEqual(data["hashtags"][0]["name"], "nairobi")
        self.assertEqual(data["posts"][0]["id"], self.p.id)
        users = self.assertOk(self.client.get(reverse("search"), {"q": "@nairobi", "type": "users", "limit": 1}))
        self.assertEqual(users["count"], 2)
        self.assertIsNotNone(users["next"])
        self.assertEqual(
            self.assertOk(self.client.get(reverse("search"), {"q": "#nairobi", "type": "posts"}))["count"], 1
        )
        self.assertError(self.client.get(reverse("search"), {"q": "x", "type": "bogus"}), 400)

    def test_private_posts_not_searchable(self):
        self.make_post(self.nv, "secret #nairobi", visibility="private")
        self.assertEqual(
            self.assertOk(self.client.get(reverse("search"), {"q": "secret", "type": "posts"}))["count"], 0
        )

    def test_recent_searches(self):
        self.assertOk(self.client.post(reverse("search-recent"), {"kind": "query", "value": "nairobi"}), 201)
        self.assertOk(self.client.post(reverse("search-recent"), {"kind": "query", "value": "nairobi"}), 201)
        self.assertEqual(len(self.assertOk(self.client.get(reverse("search-recent")))["results"]), 1)
        self.assertOk(self.client.delete(reverse("search-recent")), 204)

    def test_explore_tabs_and_overview(self):
        for_you = self.assertOk(self.client.get(reverse("explore")))["results"]
        self.assertEqual([p["id"] for p in for_you], [self.p.id])
        self.assertEqual(self.assertOk(self.client.get(reverse("explore"), {"tab": "following"}))["results"], [])
        self.assertEqual(len(self.assertOk(self.client.get(reverse("explore"), {"category": "travel"}))["results"]), 1)
        self.assertEqual(len(self.assertOk(self.client.get(reverse("explore"), {"tab": "trending"}))["results"]), 1)
        overview = self.assertOk(self.client.get(reverse("explore-overview")))
        self.assertEqual(overview["categories"][0]["slug"], "travel")
        self.assertEqual(overview["trending_hashtags"][0]["name"], "nairobi")
        self.assertIn("suggested_users", overview)
        self.assertEqual(self.assertOk(self.client.get(reverse("explore-hashtag", args=["nairobi"])))["posts_count"], 1)


class ReportTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user("owner")
        self.post = self.make_post(self.owner, "spam spam")
        self.me = self.auth(self.make_user("reporter"))

    def test_report_once_and_not_own(self):
        payload = {"target_type": "post", "target_id": self.post.id, "reason": "spam"}
        self.assertOk(self.client.post(reverse("reports"), payload), 201)
        self.assertError(self.client.post(reverse("reports"), payload), 409, "already_reported")
        self.assertOk(
            self.client.post(reverse("reports"), {"target_type": "user", "target_id": self.owner.id, "reason": "scam"}),
            201,
        )
        self.assertError(
            self.client.post(reverse("reports"), {"target_type": "user", "target_id": self.me.id, "reason": "spam"}),
            400,
        )
        self.assertError(
            self.client.post(reverse("reports"), {"target_type": "post", "target_id": 99999, "reason": "spam"}), 404
        )
        self.assertEqual(len(self.assertOk(self.client.get(reverse("report-reasons")))["results"]), len(Report.Reason))

    def test_auto_hide_threshold(self):
        from django.conf import settings

        with mock.patch.dict(settings.FLASHX, {"REPORT_AUTO_HIDE_THRESHOLD": 2}):
            self.client.post(reverse("reports"), {"target_type": "post", "target_id": self.post.id, "reason": "spam"})
            self.assertFalse(Post.objects.get(pk=self.post.pk).is_hidden)
            self.auth(self.make_user())
            self.client.post(reverse("reports"), {"target_type": "post", "target_id": self.post.id, "reason": "spam"})
            self.assertTrue(Post.objects.get(pk=self.post.pk).is_hidden)

    def test_moderator_resolution(self):
        from apps.reports.services import resolve

        r = self.assertOk(
            self.client.post(reverse("reports"), {"target_type": "post", "target_id": self.post.id, "reason": "spam"}),
            201,
        )
        admin = User.objects.create_superuser("modadmin", "mod@example.com", "Mod-pass-1234", full_name="Mod")
        resolve([r["id"]], admin, status="actioned", hide_content=True, deactivate_user=True)
        self.assertTrue(Post.objects.get(pk=self.post.pk).is_hidden)
        self.assertFalse(User.objects.get(pk=self.owner.pk).is_active)


class AdminSmokeTests(FlashXTestCase):
    """Every admin changelist and a change page render for a superuser."""

    def test_admin_pages(self):
        from django.contrib import admin

        su = User.objects.create_superuser("root_admin", "root@example.com", "Root-pass-1234", full_name="Root")
        self.make_post(su, "admin #post", media_ids=[self.make_asset(su).id])
        self.client.force_login(su)
        for model in admin.site._registry:
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(
            self.client.get(reverse("admin:posts_post_change", args=[Post.objects.first().pk])).status_code, 200
        )


class EnvelopeTests(FlashXTestCase):
    def test_health_and_unknown_route(self):
        self.assertEqual(self.assertOk(self.client.get(reverse("health")))["status"], "ok")
        self.auth(self.make_user())
        err = self.assertError(self.client.get("/api/posts/123456/"), 404, "not_found")
        self.assertIn("message", err)
        self.assertError(self.client.put(reverse("users-me"), {}), 405, "method_not_allowed")


class NotificationSenderFollowStateTests(FlashXTestCase):
    def test_sender_carries_follow_state(self):
        me = self.make_user("me2")
        fan = self.make_user("fan2")
        from apps.follows.services import follow

        follow(fan, me)
        follow(me, fan)
        self.auth(me)
        data = self.assertOk(self.client.get(reverse("notifications-list")))["results"]
        self.assertTrue(data[0]["sender"]["is_following"])
