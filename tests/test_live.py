from datetime import timedelta
from unittest import mock

import jwt
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blocks.models import Block
from apps.follows.models import Follow
from apps.live import livekit
from apps.live.models import LiveStream
from apps.notifications.models import Notification

from .base import FlashXTestCase

LK = {"URL": "wss://flashx.livekit.cloud", "API_KEY": "APIkey", "API_SECRET": "s" * 40}


@override_settings(LIVEKIT=LK)
class LiveTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.host = self.make_user("host")
        self.fan = self.make_user("fan")
        Follow.objects.create(follower=self.fan, following=self.host)
        self.server = mock.patch("requests.post").start()
        self.server.return_value.status_code = 200

    def go_live(self, title="Morning run"):
        self.auth(self.host)
        return self.assertOk(self.client.post(reverse("live-list"), {"title": title}), 201)

    def decode(self, token):
        return jwt.decode(token, LK["API_SECRET"], algorithms=["HS256"])

    def test_start_returns_host_token_and_notifies_followers(self):
        data = self.go_live()
        self.assertEqual(data["role"], "host")
        claims = self.decode(data["livekit"]["token"])
        self.assertEqual(claims["video"]["room"], data["livekit"]["room"])
        self.assertTrue(claims["video"]["canPublish"] and claims["video"]["roomAdmin"])
        self.assertEqual(claims["sub"], str(self.host.pk))
        n = Notification.objects.get(recipient=self.fan)
        self.assertEqual((n.notification_type, n.target_type), ("live", "live"))

    def test_viewer_joins_comments_and_counts(self):
        stream_id = self.go_live()["stream"]["id"]
        self.auth(self.fan)
        joined = self.assertOk(self.client.post(reverse("live-join", args=[stream_id])))
        self.assertEqual(joined["role"], "viewer")
        self.assertFalse(self.decode(joined["livekit"]["token"])["video"]["canPublish"])
        self.assertOk(self.client.post(reverse("live-comments", args=[stream_id]), {"text": "Hello!"}), 201)
        state = self.assertOk(self.client.get(reverse("live-comments", args=[stream_id])))
        self.assertEqual(state["viewer_count"], 1)
        self.assertEqual([c["text"] for c in state["comments"]], ["Hello!"])
        last = state["comments"][-1]["id"]
        self.assertEqual(
            self.assertOk(self.client.get(reverse("live-comments", args=[stream_id]), {"after": last}))["comments"], []
        )
        listing = self.assertOk(self.client.get(reverse("live-list")))
        self.assertEqual(listing["results"][0]["id"], stream_id)

    def test_invite_accept_and_remove_guest(self):
        stream_id = self.go_live()["stream"]["id"]
        self.auth(self.fan)
        self.client.post(reverse("live-join", args=[stream_id]))
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-invite", args=[stream_id]), {"user_id": self.fan.pk}), 201)
        self.auth(self.fan)
        self.assertTrue(self.assertOk(self.client.get(reverse("live-comments", args=[stream_id])))["invited"])
        accepted = self.assertOk(self.client.post(reverse("live-invite-respond", args=[stream_id]), {"accept": True}))
        self.assertEqual(accepted["role"], "guest")
        self.assertTrue(self.decode(accepted["livekit"]["token"])["video"]["canPublish"])
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-remove-guest", args=[stream_id, self.fan.pk])))
        self.assertIn("UpdateParticipant", self.server.call_args.args[0])

    def test_only_watchers_can_be_invited(self):
        stream_id = self.go_live()["stream"]["id"]
        self.assertError(
            self.client.post(reverse("live-invite", args=[stream_id]), {"user_id": self.fan.pk}), 400, "not_watching"
        )

    def test_end_and_stale_host(self):
        stream_id = self.go_live()["stream"]["id"]
        self.assertOk(self.client.post(reverse("live-end", args=[stream_id])))
        self.assertIn("DeleteRoom", self.server.call_args.args[0])
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-join", args=[stream_id])), 410, "live_ended")

        stream_id = self.go_live()["stream"]["id"]
        LiveStream.objects.filter(pk=stream_id).update(host_seen_at=timezone.now() - timedelta(minutes=5))
        self.auth(self.fan)
        self.assertEqual(self.assertOk(self.client.get(reverse("live-list")))["results"], [])
        self.assertEqual(LiveStream.objects.get(pk=stream_id).status, "ended")

    def test_blocked_users_cannot_see_or_join(self):
        stream_id = self.go_live()["stream"]["id"]
        Block.objects.create(blocker=self.host, blocked=self.fan)
        self.auth(self.fan)
        self.assertEqual(self.assertOk(self.client.get(reverse("live-list")))["results"], [])
        self.assertError(self.client.post(reverse("live-join", args=[stream_id])), 404)

    def test_only_host_can_end(self):
        stream_id = self.go_live()["stream"]["id"]
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-end", args=[stream_id])), 403)


class LiveUnconfiguredTests(FlashXTestCase):
    def test_start_is_unavailable(self):
        self.auth(self.make_user())
        self.assertFalse(livekit.is_configured())
        self.assertError(self.client.post(reverse("live-list"), {}), 503, "live_unavailable")
        self.assertFalse(self.assertOk(self.client.get(reverse("live-list")))["available"])
