from unittest import mock

from django.test import override_settings
from django.urls import reverse

from apps.follows.models import Follow
from apps.live.models import LiveBan, LiveModerationLog, LiveStream
from apps.notifications.models import Notification
from apps.reports.models import Report

from .base import FlashXTestCase

LK = {"URL": "wss://flashx.livekit.cloud", "API_KEY": "APIkey", "API_SECRET": "s" * 40}


@override_settings(LIVEKIT=LK)
class LiveRoomTests(FlashXTestCase):
    """Multi-guest rooms: requests, moderation, polls, visibility, stats."""

    def setUp(self):
        super().setUp()
        self.host = self.make_user("host")
        self.fan = self.make_user("fan")
        self.mod = self.make_user("mod")
        Follow.objects.create(follower=self.fan, following=self.host)
        self.server = mock.patch("requests.post").start()
        self.server.return_value.status_code = 200

    def go_live(self, **data):
        self.auth(self.host)
        body = {"title": "Studio session", **data}
        return self.assertOk(self.client.post(reverse("live-list"), body, format="json"), 201)["stream"]["id"]

    def watch(self, user, pk):
        self.auth(user)
        return self.assertOk(self.client.post(reverse("live-join", args=[pk])))

    def state(self, user, pk):
        self.auth(user)
        return self.assertOk(self.client.get(reverse("live-comments", args=[pk])))

    def livekit_calls(self, method):
        return [c for c in self.server.call_args_list if c.args and c.args[0].endswith(method)]

    # -- go-live form & visibility -------------------------------------------
    def test_details_category_and_audio_only(self):
        pk = self.go_live(description="Beats", category="music", audio_only=True)
        stream = self.assertOk(self.client.get(reverse("live-detail", args=[pk])))
        self.assertEqual(stream["category"], "music")
        self.assertTrue(stream["audio_only"])
        self.assertNotIn("share_url", stream)
        self.assertError(
            self.client.post(reverse("live-list"), {"category": "nonsense"}, format="json"), 400, "invalid_category"
        )
        edited = self.assertOk(self.client.patch(reverse("live-detail", args=[pk]), {"title": "New"}, format="json"))
        self.assertEqual(edited["title"], "New")
        self.auth(self.fan)
        self.assertError(self.client.patch(reverse("live-detail", args=[pk]), {"title": "x"}, format="json"), 403)

    def test_followers_only_live(self):
        pk = self.go_live(visibility="followers")
        stranger = self.make_user("stranger")
        self.auth(stranger)
        self.assertEqual(self.assertOk(self.client.get(reverse("live-list")))["results"], [])
        self.assertError(self.client.post(reverse("live-join", args=[pk])), 404)
        self.auth(self.fan)
        self.assertEqual(len(self.assertOk(self.client.get(reverse("live-list")))["results"]), 1)
        self.watch(self.fan, pk)

    def test_private_live_only_for_chosen_people(self):
        friend = self.make_user("friend")
        self.auth(self.host)
        self.assertError(
            self.client.post(reverse("live-list"), {"visibility": "private"}, format="json"), 400, "no_audience"
        )
        pk = self.go_live(visibility="private", allowed_user_ids=[friend.id])
        # Only the chosen person is notified, not followers.
        self.assertTrue(Notification.objects.filter(recipient=friend, reference_id=str(pk)).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.fan, reference_id=str(pk)).exists())
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-join", args=[pk])), 404)
        self.watch(friend, pk)

    def test_no_follower_notification_when_turned_off(self):
        pk = self.go_live(notify_followers=False)
        self.assertFalse(Notification.objects.filter(reference_id=str(pk)).exists())

    # -- guest requests -------------------------------------------------------
    def test_request_accept_and_reject(self):
        pk = self.go_live()
        self.watch(self.fan, pk)
        self.assertOk(self.client.post(reverse("live-requests", args=[pk])))
        self.assertTrue(self.state(self.fan, pk)["requested"])
        host_state = self.state(self.host, pk)
        self.assertEqual([u["id"] for u in host_state["requests"]], [self.fan.id])
        self.assertNotIn("requests", self.state(self.fan, pk))
        # Viewers can't answer requests.
        other = self.make_user("other")
        self.watch(other, pk)
        self.assertError(
            self.client.post(reverse("live-request-respond", args=[pk, self.fan.id]), {"on": True}, format="json"), 403
        )
        self.auth(self.host)
        data = self.assertOk(
            self.client.post(reverse("live-request-respond", args=[pk, self.fan.id]), {"on": True}, format="json")
        )
        self.assertEqual([g["id"] for g in data["guests"]], [self.fan.id])
        self.assertEqual(self.state(self.fan, pk)["role"], "guest")
        self.assertTrue(self.livekit_calls("UpdateParticipant"))
        joined = self.watch(self.fan, pk)
        self.assertEqual(joined["role"], "guest")
        # A second viewer is turned down.
        self.auth(other)
        self.assertOk(self.client.post(reverse("live-requests", args=[pk])))
        self.auth(self.host)
        self.assertOk(
            self.client.post(reverse("live-request-respond", args=[pk, other.id]), {"on": False}, format="json")
        )
        self.assertEqual(self.state(other, pk)["role"], "viewer")
        self.assertTrue(LiveModerationLog.objects.filter(stream_id=pk, action="reject_request").exists())

    def test_requests_turned_off_and_must_be_watching(self):
        pk = self.go_live(allow_requests=False)
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-requests", args=[pk])), 400, "requests_off")
        self.auth(self.host)
        self.client.patch(reverse("live-detail", args=[pk]), {"allow_requests": True}, format="json")
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-requests", args=[pk])), 400, "not_watching")

    @override_settings(LIVE_MAX_GUESTS=1)
    def test_guest_limit(self):
        pk = self.go_live()
        a, b = self.make_user("a"), self.make_user("b")
        for u in (a, b):
            self.watch(u, pk)
            self.assertOk(self.client.post(reverse("live-requests", args=[pk])))
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-request-respond", args=[pk, a.id]), {"on": True}, format="json"))
        self.assertError(
            self.client.post(reverse("live-request-respond", args=[pk, b.id]), {"on": True}, format="json"),
            400,
            "guest_limit",
        )

    # -- moderation -----------------------------------------------------------
    def _with_guest(self):
        pk = self.go_live()
        self.watch(self.fan, pk)
        self.assertOk(self.client.post(reverse("live-requests", args=[pk])))
        self.auth(self.host)
        self.client.post(reverse("live-request-respond", args=[pk, self.fan.id]), {"on": True}, format="json")
        return pk

    def test_mute_pin_and_remove_guest(self):
        pk = self._with_guest()
        self.auth(self.host)
        data = self.assertOk(
            self.client.post(reverse("live-mute-guest", args=[pk, self.fan.id]), {"on": True}, format="json")
        )
        self.assertTrue(data["guests"][0]["muted_by_host"])
        mute_call = self.livekit_calls("UpdateParticipant")[-1]
        self.assertNotIn("MICROPHONE", mute_call.kwargs["json"]["permission"]["canPublishSources"])
        self.assertTrue(self.state(self.fan, pk)["muted_by_host"])
        self.auth(self.host)
        data = self.assertOk(self.client.post(reverse("live-pin", args=[pk]), {"user_id": self.fan.id}, format="json"))
        self.assertEqual(data["pinned_user_id"], self.fan.id)
        self.assertOk(self.client.post(reverse("live-remove-guest", args=[pk, self.fan.id])))
        state = self.state(self.host, pk)
        self.assertEqual(state["guests"], [])
        self.assertIsNone(state["pinned_user_id"])
        actions = set(LiveModerationLog.objects.filter(stream_id=pk).values_list("action", flat=True))
        self.assertTrue({"mute", "remove_guest"} <= actions)

    def test_guest_cannot_mute_others_or_pin(self):
        pk = self._with_guest()
        self.auth(self.fan)
        self.assertError(
            self.client.post(reverse("live-mute-guest", args=[pk, self.fan.id]), {"on": True}, format="json"), 403
        )
        self.assertError(self.client.post(reverse("live-pin", args=[pk]), {"user_id": self.fan.id}, format="json"), 403)

    def test_moderator_powers_and_limits(self):
        pk = self._with_guest()
        self.watch(self.mod, pk)
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-moderators", args=[pk]), {"user_id": self.mod.id}, format="json"))
        self.assertTrue(self.state(self.mod, pk)["is_moderator"])
        self.auth(self.mod)
        self.assertOk(self.client.post(reverse("live-mute-guest", args=[pk, self.fan.id]), {"on": True}, format="json"))
        # Moderators can't pin guests, end the live or appoint moderators.
        self.assertError(self.client.post(reverse("live-pin", args=[pk]), {"user_id": self.fan.id}, format="json"), 403)
        self.assertError(self.client.post(reverse("live-end", args=[pk])), 403)
        self.assertError(
            self.client.post(reverse("live-moderators", args=[pk]), {"user_id": self.fan.id}, format="json"), 403
        )
        self.assertError(
            self.client.post(reverse("live-bans", args=[pk]), {"user_id": self.host.id}, format="json"), 403
        )

    def test_ban_kicks_and_blocks_rejoining(self):
        pk = self._with_guest()
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-bans", args=[pk]), {"user_id": self.fan.id}, format="json"))
        self.assertTrue(LiveBan.objects.filter(stream_id=pk, user=self.fan).exists())
        self.assertTrue(self.livekit_calls("RemoveParticipant"))
        self.auth(self.fan)
        self.assertError(self.client.post(reverse("live-join", args=[pk])), 403, "live_banned")
        self.assertError(self.client.post(reverse("live-comments", args=[pk]), {"text": "hi"}), 403, "live_banned")
        self.auth(self.host)
        self.assertOk(self.client.delete(reverse("live-bans", args=[pk]), {"user_id": self.fan.id}, format="json"))
        self.watch(self.fan, pk)

    # -- comments, questions, polls, reactions -------------------------------
    def test_replies_questions_pin_and_remove_comments(self):
        pk = self.go_live()
        self.watch(self.fan, pk)
        c1 = self.assertOk(self.client.post(reverse("live-comments", args=[pk]), {"text": "first!"}), 201)
        q = self.assertOk(
            self.client.post(reverse("live-comments", args=[pk]), {"text": "Which mic?", "kind": "question"}), 201
        )
        self.auth(self.host)
        reply = self.assertOk(
            self.client.post(reverse("live-comments", args=[pk]), {"text": "thanks", "reply_to_id": c1["id"]}), 201
        )
        self.assertEqual(reply["reply_to"]["id"], c1["id"])
        state = self.state(self.host, pk)
        self.assertEqual([x["id"] for x in state["questions"]], [q["id"]])
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-comment-answer", args=[pk, q["id"]])))
        self.assertEqual(self.state(self.host, pk)["questions"], [])
        self.auth(self.host)
        data = self.assertOk(self.client.post(reverse("live-pin", args=[pk]), {"comment_id": c1["id"]}, format="json"))
        self.assertEqual(data["pinned_comment"]["id"], c1["id"])
        other = self.make_user("other")
        self.watch(other, pk)
        self.assertError(self.client.delete(reverse("live-comment-detail", args=[pk, c1["id"]])), 403)
        self.auth(self.host)
        self.assertOk(self.client.delete(reverse("live-comment-detail", args=[pk, c1["id"]])), 204)
        state = self.state(self.fan, pk)
        self.assertNotIn(c1["id"], [c["id"] for c in state["comments"]])
        self.assertIn(c1["id"], state["removed_comment_ids"])
        self.assertIsNone(state["pinned_comment"])

    def test_polls(self):
        pk = self.go_live()
        self.watch(self.fan, pk)
        self.assertError(
            self.client.post(reverse("live-polls", args=[pk]), {"question": "?", "options": ["a", "b"]}, format="json"),
            403,
        )
        self.auth(self.host)
        self.assertError(
            self.client.post(
                reverse("live-polls", args=[pk]), {"question": "Next?", "options": ["a", "a"]}, format="json"
            ),
            400,
            "invalid_poll",
        )
        state = self.assertOk(
            self.client.post(
                reverse("live-polls", args=[pk]), {"question": "Next song?", "options": ["Jazz", "Rock"]}, format="json"
            ),
            201,
        )
        poll_id = state["poll"]["id"]
        self.auth(self.fan)
        voted = self.assertOk(self.client.post(reverse("live-poll-vote", args=[pk, poll_id]), {"option": 1}))
        self.assertEqual(voted["poll"]["my_vote"], 1)
        voted = self.assertOk(self.client.post(reverse("live-poll-vote", args=[pk, poll_id]), {"option": 0}))
        self.assertEqual(voted["poll"]["total_votes"], 1)
        self.assertEqual(voted["poll"]["options"][0]["percent"], 100)
        self.assertError(self.client.post(reverse("live-poll-vote", args=[pk, poll_id]), {"option": 5}), 400)
        self.auth(self.host)
        self.assertOk(self.client.post(reverse("live-poll-close", args=[pk, poll_id])))
        self.auth(self.fan)
        self.assertError(
            self.client.post(reverse("live-poll-vote", args=[pk, poll_id]), {"option": 0}), 400, "poll_closed"
        )

    def test_reactions_pause_and_stats(self):
        pk = self.go_live()
        self.watch(self.fan, pk)
        self.assertOk(self.client.post(reverse("live-reactions", args=[pk]), {"count": 7}))
        self.assertError(self.client.get(reverse("live-stats", args=[pk])), 403)
        self.assertError(self.client.post(reverse("live-pause", args=[pk]), {"on": True}), 403)
        self.auth(self.host)
        self.assertTrue(self.assertOk(self.client.post(reverse("live-pause", args=[pk]), {"on": True}))["is_paused"])
        self.assertTrue(self.state(self.fan, pk)["is_paused"])
        self.auth(self.host)
        self.client.post(reverse("live-pause", args=[pk]), {"on": False})
        self.client.post(reverse("live-end", args=[pk]))
        stats = self.assertOk(self.client.get(reverse("live-stats", args=[pk])))
        self.assertEqual(stats["reactions"], 7)
        self.assertEqual(stats["total_viewers"], 1)
        self.assertIn("average_watch_seconds", stats)

    def test_report_live(self):
        pk = self.go_live()
        self.auth(self.fan)
        self.assertOk(self.client.post(reverse("live-report", args=[pk]), {"reason": "spam"}), 201)
        report = Report.objects.get()
        self.assertEqual(report.reported_user, self.host)
        self.assertIn(f"Live #{pk}", report.details)
        self.auth(self.host)
        self.assertError(self.client.post(reverse("live-report", args=[pk]), {"reason": "spam"}), 400)

    def test_share_page(self):
        pk = self.go_live()
        page = self.client.get(f"/l/{pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"flashx://open/l/{pk}", page.content.decode())
        LiveStream.objects.filter(pk=pk).update(visibility="followers")
        self.assertEqual(self.client.get(f"/l/{pk}/").status_code, 404)
