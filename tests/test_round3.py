from datetime import timedelta
from unittest import mock

import jwt
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blocks.models import Block
from apps.calls.models import Call
from apps.follows.models import Follow, FollowRequest
from apps.live import livekit
from apps.messaging.models import Conversation, Message
from apps.messaging.services import create_group, get_or_create_direct, send_message
from apps.music import services as music
from apps.music.models import Sound
from apps.notifications import push
from apps.notifications.models import DeviceToken, Notification

from .base import FlashXTestCase

LK = {"URL": "wss://flashx.livekit.cloud", "API_KEY": "APIkey", "API_SECRET": "s" * 40}
JAMENDO = {"JAMENDO_CLIENT_ID": "test-client", "COMMERCIAL_USE": True, "OPENVERSE_ENABLED": False}
OPENVERSE = {"JAMENDO_CLIENT_ID": "", "COMMERCIAL_USE": True, "OPENVERSE_ENABLED": True}
OV_ID = "0b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d"


def openverse_track(track_id=OV_ID, title="Savanna Sunrise", license="by", filetype="mp3", mature=False):
    return {
        "id": track_id,
        "title": title,
        "creator": "Kito Beats",
        "url": f"https://prod-1.storage.jamendo.com/download/track/{title.replace(' ', '')}.{filetype}",
        "filetype": filetype,
        "duration": 201000,
        "license": license,
        "license_version": "4.0",
        "license_url": f"https://creativecommons.org/licenses/{license}/4.0/",
        "foreign_landing_url": "https://www.jamendo.com/track/1",
        "thumbnail": f"https://api.openverse.org/v1/audio/{track_id}/thumb/",
        "genres": ["afrobeat"],
        "source": "jamendo",
        "mature": mature,
    }


def jamendo_track(track_id=1, title="Nairobi Nights", ccnc="false", ccnd="false"):
    return {
        "id": str(track_id),
        "name": title,
        "artist_name": "Sauti Band",
        "image": "https://usercontent.jamendo.com/cover.jpg",
        "audio": f"https://prod-1.storage.jamendo.com/?trackid={track_id}&format=mp32",
        "duration": 184,
        "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/",
        "shareurl": f"https://www.jamendo.com/track/{track_id}",
        "licenses": {"ccnc": ccnc, "ccnd": ccnd, "ccsa": "true"},
        "musicinfo": {"tags": {"genres": ["afro"]}},
    }


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
class GroupListTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.make_user("ama", full_name="Ama Mensah")
        self.b = self.make_user("ben", full_name="Ben Otieno")
        self.c = self.make_user("cate", full_name="Cate Wanjiru")

    def chat_ids(self, user):
        self.auth(user)
        return [c["id"] for c in self.assertOk(self.client.get(reverse("messages-conversations")))["results"]]

    def test_new_group_without_messages_is_listed_for_every_member(self):
        self.auth(self.a)
        data = self.assertOk(
            self.client.post(
                reverse("messages-start"), {"participant_ids": [self.b.pk, self.c.pk], "title": "Weekend"}
            ),
            201,
        )
        for user in (self.a, self.b, self.c):
            self.assertEqual(self.chat_ids(user), [data["id"]])
        self.auth(self.b)
        row = self.assertOk(self.client.get(reverse("messages-conversations")))["results"][0]
        self.assertEqual(row["last_message"]["text"], 'Ama created the group "Weekend"')
        self.assertEqual(row["unread_count"], 1)
        # Created once: no duplicate rows.
        self.assertEqual(Conversation.objects.filter(is_group=True).count(), 1)

    def test_old_groups_without_activity_reappear(self):
        convo = create_group(self.a, [self.b.pk, self.c.pk], "Old")
        Message.objects.filter(conversation=convo).delete()
        Conversation.objects.filter(pk=convo.pk).update(last_message_at=None)
        self.assertEqual(self.chat_ids(self.b), [str(convo.pk)])

    def test_membership_changes_are_recorded_and_synced(self):
        convo = create_group(self.a, [self.b.pk, self.c.pk], "Team")
        d = self.make_user("dan", full_name="Dan Kip")
        self.auth(self.a)
        self.assertOk(self.client.post(reverse("messages-members", args=[convo.pk]), {"user_ids": [d.pk]}), 201)
        self.assertIn(str(convo.pk), self.chat_ids(d))
        self.assertTrue(Notification.objects.filter(recipient=d, notification_type="group_add").exists())
        self.auth(self.a)
        self.assertOk(self.client.delete(reverse("messages-member-detail", args=[convo.pk, self.c.pk])), 204)
        self.assertNotIn(str(convo.pk), self.chat_ids(self.c))
        events = list(Message.objects.filter(conversation=convo, kind="system").values_list("content", flat=True))
        self.assertEqual(events, ["removed Cate", "added Dan", 'created the group "Team"'])

    def test_admin_renames_and_sets_photo(self):
        convo = create_group(self.a, [self.b.pk, self.c.pk], "Team")
        asset = self.make_asset(self.a, purpose="group")
        self.auth(self.a)
        data = self.assertOk(
            self.client.patch(reverse("messages-info", args=[convo.pk]), {"title": "Squad", "image_id": asset.pk})
        )
        self.assertEqual(data["title"], "Squad")
        self.assertIsNotNone(data["avatar"])
        self.auth(self.b)
        self.assertError(self.client.patch(reverse("messages-info", args=[convo.pk]), {"title": "Mine"}), 403)


# ---------------------------------------------------------------------------
# Delete for me
# ---------------------------------------------------------------------------
class DeleteForMeTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make_user(), self.make_user()
        self.convo = get_or_create_direct(self.a, self.b)
        self.msg = send_message(self.b, self.convo, content="secret plan")

    def thread(self, user):
        self.auth(user)
        return self.assertOk(self.client.get(reverse("messages-thread", args=[self.convo.pk])))["results"]

    def test_hidden_only_for_me_and_persists(self):
        self.auth(self.a)
        url = reverse("messages-delete", args=[self.msg.pk]) + "?for=me"
        self.assertOk(self.client.delete(url), 204)
        self.assertEqual(self.thread(self.a), [])
        self.assertEqual([m["content"] for m in self.thread(self.b)], ["secret plan"])
        self.msg.refresh_from_db()
        self.assertFalse(self.msg.is_deleted)
        self.auth(self.a)
        row = self.assertOk(self.client.get(reverse("messages-conversations")))["results"][0]
        self.assertIsNone(row["last_message"])

    def test_delete_for_everyone_unchanged_and_sender_only(self):
        self.auth(self.a)
        self.assertError(self.client.delete(reverse("messages-delete", args=[self.msg.pk])), 403)
        self.auth(self.b)
        self.assertOk(self.client.delete(reverse("messages-delete", args=[self.msg.pk])), 204)
        self.assertTrue(self.thread(self.a)[0]["is_deleted"])

    def test_works_in_groups(self):
        c = self.make_user()
        group = create_group(self.a, [self.b.pk, c.pk])
        msg = send_message(self.b, group, content="hello all")
        self.auth(c)
        self.assertOk(self.client.delete(reverse("messages-delete", args=[msg.pk]) + "?for=me"), 204)
        texts = [
            m["content"] for m in self.assertOk(self.client.get(reverse("messages-thread", args=[group.pk])))["results"]
        ]
        self.assertNotIn("hello all", texts)
        self.auth(self.a)
        texts = [
            m["content"] for m in self.assertOk(self.client.get(reverse("messages-thread", args=[group.pk])))["results"]
        ]
        self.assertIn("hello all", texts)


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------
class PresenceTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make_user(), self.make_user()
        self.convo = get_or_create_direct(self.a, self.b)
        send_message(self.a, self.convo, content="hi")

    def peer(self, viewer):
        self.auth(viewer)
        return self.assertOk(self.client.get(reverse("messages-info", args=[self.convo.pk])))["participants"][0]

    def test_online_offline_and_last_seen(self):
        self.auth(self.b)
        self.assertOk(self.client.post(reverse("users-presence"), {"state": "online"}))
        p = self.peer(self.a)
        self.assertTrue(p["is_online"])
        self.assertIsNotNone(p["last_seen_at"])
        self.auth(self.b)
        self.assertOk(self.client.post(reverse("users-presence"), {"state": "offline"}))
        p = self.peer(self.a)
        self.assertFalse(p["is_online"])
        self.assertIsNotNone(p["last_seen_at"])

    def test_privacy_switch_hides_both_ways(self):
        self.auth(self.b)
        self.client.post(reverse("users-presence"), {"state": "online"})
        self.assertOk(self.client.patch(reverse("users-me"), {"show_activity_status": False}))
        p = self.peer(self.a)
        self.assertEqual((p["is_online"], p["last_seen_at"]), (False, None))
        # Someone who hides theirs doesn't see others' either.
        self.auth(self.a)
        self.client.post(reverse("users-presence"), {"state": "online"})
        p = self.peer(self.b)
        self.assertEqual((p["is_online"], p["last_seen_at"]), (False, None))

    def test_stale_heartbeat_reads_as_offline(self):
        type(self.b).objects.filter(pk=self.b.pk).update(
            presence_online=True, last_seen_at=timezone.now() - timedelta(minutes=10)
        )
        p = self.peer(self.a)
        self.assertFalse(p["is_online"])
        self.assertIsNotNone(p["last_seen_at"])


# ---------------------------------------------------------------------------
# Blocking privacy
# ---------------------------------------------------------------------------
class BlockPrivacyTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.make_user("blocker")
        self.them = self.make_user("blocked")
        Follow.objects.create(follower=self.me, following=self.them)
        Follow.objects.create(follower=self.them, following=self.me)
        self.convo = get_or_create_direct(self.me, self.them)
        send_message(self.them, self.convo, content="before")
        self.auth(self.me)
        self.assertOk(self.client.post(reverse("users-block", args=[self.them.pk])), 201)

    def test_blocked_person_gets_no_notification_or_flag(self):
        self.assertFalse(Notification.objects.filter(recipient=self.them).exclude(notification_type="message").exists())
        self.auth(self.them)
        info = self.assertOk(self.client.get(reverse("messages-info", args=[self.convo.pk])))
        self.assertFalse(info["is_blocked"])
        self.assertError(self.client.get(reverse("users-detail", args=[self.me.pk])), 404)

    def test_direct_api_requests_are_refused_without_revealing(self):
        self.auth(self.them)
        for method, url, body in (
            ("post", reverse("users-follow", args=[self.me.pk]), {}),
            ("post", reverse("messages-conversations"), {"recipient_id": self.me.pk, "content": "x"}),
            ("post", reverse("messages-start"), {"recipient_id": self.me.pk}),
        ):
            err = self.assertError(getattr(self.client, method)(url, body), 404, "user_unavailable")
            self.assertNotIn("block", err["message"].lower())

    @override_settings(LIVEKIT=LK)
    def test_calls_refused_both_ways(self):
        self.auth(self.them)
        self.assertError(self.client.post(reverse("calls"), {"user_id": self.me.pk}), 404, "user_unavailable")
        self.auth(self.me)
        self.assertError(self.client.post(reverse("calls"), {"user_id": self.them.pk}), 403, "blocked")

    def test_unblock_restores(self):
        self.auth(self.me)
        self.assertOk(self.client.delete(reverse("users-block", args=[self.them.pk])))
        self.auth(self.them)
        self.assertOk(self.client.get(reverse("users-detail", args=[self.me.pk])))
        self.assertOk(
            self.client.post(
                reverse("messages-conversations"), {"conversation_id": str(self.convo.pk), "content": "hey"}
            ),
            201,
        )

    def test_search_and_lists_hide_blocker(self):
        self.auth(self.them)
        res = self.assertOk(self.client.get(reverse("search"), {"q": "blocker", "type": "users"}))
        self.assertEqual([u["id"] for u in res["results"]], [])


# ---------------------------------------------------------------------------
# Private accounts
# ---------------------------------------------------------------------------
class PrivateAccountTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user("private_one", is_private=True)
        self.fan = self.make_user("fan_one")
        self.post = self.make_post(self.owner, caption="only followers")

    def test_request_approve_flow(self):
        self.auth(self.fan)
        self.assertError(self.client.get(reverse("posts-detail", args=[self.post.pk])), 404)
        res = self.assertOk(self.client.post(reverse("users-follow", args=[self.owner.pk])), 201)
        self.assertEqual(res["follow_status"], "requested")
        profile = self.assertOk(self.client.get(reverse("users-detail", args=[self.owner.pk])))
        self.assertEqual((profile["follow_status"], profile["can_view_content"]), ("requested", False))
        self.auth(self.owner)
        pending = self.assertOk(self.client.get(reverse("users-follow-requests")))["results"]
        self.assertEqual([u["id"] for u in pending], [self.fan.pk])
        self.assertOk(self.client.post(reverse("users-follow-request-action", args=[self.fan.pk, "approve"])))
        self.auth(self.fan)
        self.assertOk(self.client.get(reverse("posts-detail", args=[self.post.pk])))
        self.assertTrue(Notification.objects.filter(recipient=self.fan, notification_type="follow_accepted").exists())

    def test_decline_and_cancel(self):
        self.auth(self.fan)
        self.client.post(reverse("users-follow", args=[self.owner.pk]))
        self.assertOk(self.client.delete(reverse("users-follow", args=[self.owner.pk])))
        self.assertFalse(FollowRequest.objects.exists())
        self.client.post(reverse("users-follow", args=[self.owner.pk]))
        self.auth(self.owner)
        self.assertOk(self.client.post(reverse("users-follow-request-action", args=[self.fan.pk, "decline"])))
        self.assertFalse(Follow.objects.filter(follower=self.fan).exists())

    def test_going_public_approves_pending(self):
        self.auth(self.fan)
        self.client.post(reverse("users-follow", args=[self.owner.pk]))
        self.auth(self.owner)
        self.assertOk(self.client.patch(reverse("users-me"), {"is_private": False}))
        self.assertTrue(Follow.objects.filter(follower=self.fan, following=self.owner).exists())


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
@override_settings(LIVEKIT=LK)
class CallTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.ama, self.ben = self.make_user("ama_c"), self.make_user("ben_c")
        Follow.objects.create(follower=self.ama, following=self.ben)
        Follow.objects.create(follower=self.ben, following=self.ama)
        self.http = mock.patch("requests.post", return_value=FakeResponse()).start()

    def ring(self, kind="video"):
        self.auth(self.ama)
        return self.assertOk(self.client.post(reverse("calls"), {"user_id": self.ben.pk, "kind": kind}), 201)

    def test_full_call_flow(self):
        data = self.ring()
        call_id = data["call"]["id"]
        self.assertEqual(data["call"]["status"], "ringing")
        claims = jwt.decode(data["livekit"]["token"], LK["API_SECRET"], algorithms=["HS256"])
        self.assertTrue(claims["video"]["roomJoin"] and claims["video"]["canPublish"])
        self.assertEqual(claims["video"]["room"], data["livekit"]["room"])
        self.auth(self.ben)
        accepted = self.assertOk(self.client.post(reverse("calls-accept", args=[call_id])))
        self.assertEqual(accepted["call"]["status"], "accepted")
        self.assertEqual(accepted["livekit"]["room"], data["livekit"]["room"])
        ended = self.assertOk(self.client.post(reverse("calls-end", args=[call_id])))
        self.assertEqual(ended["call"]["status"], "ended")
        history = self.assertOk(self.client.get(reverse("calls")))["results"]
        self.assertEqual(history[0]["id"], call_id)

    def test_incoming_signal_is_a_silent_high_priority_push(self):
        DeviceToken.objects.create(user=self.ben, token="ben-phone", platform="android")
        with (
            mock.patch.object(push, "is_enabled", return_value=True),
            mock.patch.object(push, "_access_token", return_value=("t", "proj")),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.ring()
        msgs = [c.kwargs["json"]["message"] for c in self.http.call_args_list if "json" in c.kwargs]
        incoming = [m for m in msgs if m.get("data", {}).get("event") == "incoming"]
        self.assertEqual(len(incoming), 1)
        self.assertEqual(incoming[0]["data"]["sync"], "call")
        self.assertEqual(incoming[0]["android"]["priority"], "HIGH")
        self.assertNotIn("notification", incoming[0])

    def test_decline_cancel_and_missed(self):
        call_id = self.ring()["call"]["id"]
        self.auth(self.ben)
        self.assertEqual(
            self.assertOk(self.client.post(reverse("calls-decline", args=[call_id])))["call"]["status"], "declined"
        )

        call_id = self.ring()["call"]["id"]
        self.assertEqual(
            self.assertOk(self.client.post(reverse("calls-cancel", args=[call_id])))["call"]["status"], "cancelled"
        )
        self.assertTrue(Notification.objects.filter(recipient=self.ben, notification_type="missed_call").exists())

        call_id = self.ring()["call"]["id"]
        Call.objects.filter(pk=call_id).update(created_at=timezone.now() - timedelta(minutes=2))
        self.auth(self.ben)
        self.assertEqual(
            self.assertOk(self.client.get(reverse("calls-detail", args=[call_id])))["call"]["status"], "missed"
        )
        self.auth(self.ben)
        self.assertError(self.client.post(reverse("calls-accept", args=[call_id])), 410, "call_ended")

    def test_busy_and_already_in_call(self):
        carl = self.make_user("carl_c")
        Follow.objects.create(follower=carl, following=self.ben)
        Follow.objects.create(follower=self.ben, following=carl)
        self.ring()
        self.auth(carl)
        busy = self.assertOk(self.client.post(reverse("calls"), {"user_id": self.ben.pk}), 201)
        self.assertEqual(busy["call"]["status"], "busy")
        self.assertNotIn("livekit", busy)
        self.auth(self.ama)
        self.assertError(self.client.post(reverse("calls"), {"user_id": self.ben.pk}), 409, "already_in_call")

    def test_eligibility(self):
        stranger = self.make_user()
        self.auth(stranger)
        self.assertError(self.client.post(reverse("calls"), {"user_id": self.ben.pk}), 403, "not_mutual")
        type(self.ben).objects.filter(pk=self.ben.pk).update(allow_calls=False)
        self.auth(self.ama)
        self.assertError(self.client.post(reverse("calls"), {"user_id": self.ben.pk}), 403, "calls_off")
        self.auth(stranger)
        call = Call.objects.create(caller=self.ama, callee=self.ben)
        self.assertError(self.client.get(reverse("calls-detail", args=[call.pk])), 404)

    def test_unconfigured(self):
        with override_settings(LIVEKIT={"URL": "", "API_KEY": "", "API_SECRET": ""}):
            self.auth(self.ama)
            self.assertError(self.client.post(reverse("calls"), {"user_id": self.ben.pk}), 503, "calls_unavailable")

    def test_profile_exposes_can_call(self):
        self.auth(self.ama)
        self.assertTrue(self.assertOk(self.client.get(reverse("users-detail", args=[self.ben.pk])))["can_call"])


class LiveKitAdminTokenTests(FlashXTestCase):
    @override_settings(LIVEKIT=LK)
    def test_room_delete_token_has_room_create(self):
        token = livekit._admin_token("live-x")
        grant = jwt.decode(token, LK["API_SECRET"], algorithms=["HS256"])["video"]
        self.assertTrue(grant["roomAdmin"] and grant["roomCreate"])

    def test_health_reports_features(self):
        data = self.assertOk(self.client.get(reverse("health")))
        self.assertIn("features", data)
        self.assertEqual(data["features"]["live"], livekit.is_configured())

    def test_health_reports_pending_migrations(self):
        from unittest import mock

        from apps.core import views as core_views

        self.assertEqual(self.assertOk(self.client.get(reverse("health")))["migrations_pending"], 0)
        with (
            mock.patch.object(core_views, "_schema_current", False),
            mock.patch(
                "django.db.migrations.executor.MigrationExecutor.migration_plan", return_value=[object(), object()]
            ),
        ):
            response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        details = response.json()["error"]["details"]
        self.assertEqual(details["migrations_pending"], 2)
        self.assertEqual(details["status"], "degraded")


# ---------------------------------------------------------------------------
# Sounds
# ---------------------------------------------------------------------------
@override_settings(MUSIC=JAMENDO)
class SoundTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.user = self.auth(self.make_user("maker"))

    def test_search_filters_licences(self):
        tracks = [
            jamendo_track(1),
            jamendo_track(2, "NoDerivs", ccnd="true"),
            jamendo_track(3, "NonCommercial", ccnc="true"),
        ]
        with mock.patch.object(music, "_jamendo", return_value=tracks) as api:
            data = self.assertOk(self.client.get(reverse("music-search"), {"q": "nairobi", "genre": "afro"}))
        self.assertEqual([t["title"] for t in data["catalogue"]], ["Nairobi Nights"])
        t = data["catalogue"][0]
        self.assertEqual((t["id"], t["license_name"]), ("jamendo:1", "CC BY-SA 3.0"))
        self.assertIn("Jamendo", t["attribution"])
        params = api.call_args.args[0]
        self.assertEqual((params["search"], params["fuzzytags"]), ("nairobi", "afro"))

    def test_post_with_catalogue_sound_section_and_volume(self):
        with mock.patch.object(music, "_jamendo", return_value=[jamendo_track(7)]):
            data = self.assertOk(
                self.client.post(
                    reverse("posts-list"),
                    {"caption": "vibes", "sound_id": "jamendo:7", "sound_start": 42.5, "sound_volume": 0.6},
                ),
                201,
            )
        s = data["sound"]
        self.assertEqual(
            (s["title"], s["start"], s["volume"], s["plays_separately"]), ("Nairobi Nights", 42.5, 0.6, True)
        )
        self.assertEqual(data["music_title"], "Nairobi Nights - Sauti Band")
        sound = Sound.objects.get(source="jamendo", external_id="7")
        self.assertEqual(sound.uses_count, 1)
        posts = self.assertOk(self.client.get(reverse("music-sound-posts", args=[sound.pk])))["results"]
        self.assertEqual([p["id"] for p in posts], [data["id"]])

    def test_change_and_remove_sound(self):
        with mock.patch.object(music, "_jamendo", return_value=[jamendo_track(8)]):
            post = self.assertOk(
                self.client.post(reverse("posts-list"), {"caption": "x", "sound_id": "jamendo:8"}), 201
            )
        sound = Sound.objects.get(external_id="8")
        data = self.assertOk(
            self.client.patch(reverse("posts-detail", args=[post["id"]]), {"sound_id": None}, format="json")
        )
        self.assertIsNone(data["sound"])
        sound.refresh_from_db()
        self.assertEqual(sound.uses_count, 0)

    def test_video_post_creates_reusable_original_sound(self):
        video = self.make_asset(self.user, purpose="post", resource_type="video")
        post = self.assertOk(
            self.client.post(reverse("posts-list"), {"caption": "dance", "media_ids": [video.pk]}), 201
        )
        self.assertTrue(post["sound"]["is_original"])
        self.assertFalse(post["sound"]["plays_separately"])
        self.assertTrue(post["sound"]["audio_url"].endswith(".mp3"))
        other = self.auth(self.make_user("reuser"))
        with mock.patch.object(music, "_jamendo", return_value=[]):
            found = self.assertOk(self.client.get(reverse("music-search"), {"q": "maker"}))["original"]
        self.assertEqual(
            [
                s["id"]
                for s in self.assertOk(self.client.get(reverse("search"), {"q": "maker", "type": "sounds"}))["results"]
            ],
            [post["sound"]["id"]],
        )
        self.assertEqual([s["id"] for s in found], [post["sound"]["id"]])
        reel_asset = self.make_asset(other, purpose="reel", resource_type="video")
        reel = self.assertOk(
            self.client.post(reverse("reels-list"), {"media_id": reel_asset.pk, "sound_id": post["sound"]["id"]}), 201
        )
        self.assertTrue(reel["sound"]["plays_separately"])
        reels = self.assertOk(self.client.get(reverse("music-sound-reels", args=[post["sound"]["id"]])))["results"]
        self.assertEqual([r["id"] for r in reels], [reel["id"]])

    def test_reuse_can_be_turned_off_and_blocked_owner_hidden(self):
        video = self.make_asset(self.user, purpose="post", resource_type="video")
        post = self.assertOk(
            self.client.post(
                reverse("posts-list"), {"caption": "x", "media_ids": [video.pk], "allow_sound_reuse": False}
            ),
            201,
        )
        other = self.auth(self.make_user())
        self.assertError(
            self.client.post(reverse("posts-list"), {"caption": "y", "sound_id": post["sound"]["id"]}),
            400,
            "sound_unavailable",
        )
        Block.objects.create(blocker=self.user, blocked=other)
        self.assertError(self.client.get(reverse("music-sound", args=[post["sound"]["id"]])), 404)

    def test_legacy_store_preview_is_rejected(self):
        self.assertError(
            self.client.post(
                reverse("posts-list"),
                {"caption": "x", "sound": {"id": "deezer:1", "title": "t", "preview_url": "https://x"}},
                format="json",
            ),
            400,
            "sound_unavailable",
        )

    def test_without_catalogue_only_original_sounds(self):
        with override_settings(MUSIC={"JAMENDO_CLIENT_ID": "", "OPENVERSE_ENABLED": False}):
            data = self.assertOk(self.client.get(reverse("music-search"), {"q": "x"}))
        self.assertEqual((data["catalogue"], data["catalogue_enabled"]), ([], False))

    def test_genres(self):
        self.assertIn("hiphop", [g["id"] for g in self.assertOk(self.client.get(reverse("music-genres")))["results"]])


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------
class NotificationPrefTests(FlashXTestCase):
    def test_switches_are_saved_and_respected(self):
        user = self.auth(self.make_user())
        data = self.assertOk(
            self.client.patch(reverse("users-me"), {"notification_prefs": {"likes": False}}, format="json")
        )
        self.assertEqual(data["notification_prefs"], {"likes": False})
        user.refresh_from_db()
        self.assertFalse(push.wants_push(user, "like"))
        self.assertTrue(push.wants_push(user, "comment"))
        self.assertError(
            self.client.patch(reverse("users-me"), {"notification_prefs": {"bogus": True}}, format="json"), 400
        )


class SharePageTests(FlashXTestCase):
    def test_public_post_page_and_private_hidden(self):
        author = self.make_user("sharer", full_name="Sharer One")
        post = self.make_post(author, caption="Sunset at the coast")
        res = self.client.get(f"/p/{post.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"og:title", res.content)
        self.assertIn(b"Sunset at the coast", res.content)
        type(author).objects.filter(pk=author.pk).update(is_private=True)
        self.assertEqual(self.client.get(f"/p/{post.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/u/sharer/").status_code, 200)
        self.assertEqual(self.client.get("/u/nobody-here/").status_code, 404)


@override_settings(MUSIC=OPENVERSE)
class OpenverseSoundTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.user = self.auth(self.make_user("ovmaker"))

    def test_search_without_any_key_returns_licensed_music(self):
        rows = [
            openverse_track(),
            openverse_track("1b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "No Commercial", license="by-nc"),
            openverse_track("2b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "No Derivs", license="by-nd"),
            openverse_track("3b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "Ogg Only", filetype="ogg"),
            openverse_track("4b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "Mature", mature=True),
        ]
        with mock.patch.object(music, "_openverse", return_value={"results": rows}) as api:
            data = self.assertOk(self.client.get(reverse("music-search"), {"q": "afro"}))
        self.assertTrue(data["catalogue_enabled"])
        self.assertEqual([t["title"] for t in data["catalogue"]], ["Savanna Sunrise"])
        t = data["catalogue"][0]
        self.assertEqual((t["id"], t["license_name"], t["duration"]), (f"openverse:{OV_ID}", "CC BY 4.0", 201.0))
        self.assertIn("Kito Beats", t["attribution"])
        params = api.call_args.args[1]
        self.assertEqual(params["category"], "music")
        self.assertNotIn("by-nc", params["license"].split(","))

    def test_jamendo_mp3_streams_without_extension_are_kept(self):
        jamendo = openverse_track("5b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "Jamendo Stream", filetype="mp32")
        jamendo["url"] = "https://prod-1.storage.jamendo.com/?trackid=123&format=mp31&from=app"
        no_type = openverse_track("6b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "No Type", filetype="")
        no_type["url"] = "https://prod-1.storage.jamendo.com/?trackid=456&format=mp31"
        flac = openverse_track("7b3f8c3e-4c0a-4bfa-9d4e-6f7a1a2b3c4d", "Flac", filetype="flac")
        with mock.patch.object(music, "_openverse", return_value={"results": [jamendo, no_type, flac]}):
            data = self.assertOk(self.client.get(reverse("music-search"), {"q": "stream"}))
        titles = [t["title"] for t in data["catalogue"]]
        self.assertEqual(titles, ["Jamendo Stream", "No Type"])

    def test_rate_limited_search_serves_last_good_results(self):
        with mock.patch.object(music, "_openverse", return_value={"results": [openverse_track()]}):
            with mock.patch.object(music.cache, "set", wraps=music.cache.set) as setter:
                self.assertOk(self.client.get(reverse("music-search"), {"q": "savanna"}))
        fresh_key = next(
            c.args[0]
            for c in setter.call_args_list
            if c.args[0].startswith("music:openverse:category=") and not c.args[0].endswith(":stale")
        )
        music.cache.delete(fresh_key)
        with mock.patch.object(music, "_openverse", side_effect=RuntimeError("HTTP 429")):
            data = self.assertOk(self.client.get(reverse("music-search"), {"q": "savanna"}))
        self.assertEqual([t["title"] for t in data["catalogue"]], ["Savanna Sunrise"])

    def test_post_with_openverse_sound_is_reusable(self):
        with mock.patch.object(music, "_openverse", return_value=openverse_track()) as api:
            post = self.assertOk(
                self.client.post(reverse("posts-list"), {"caption": "x", "sound_id": f"openverse:{OV_ID}"}), 201
            )
            api.assert_called_once_with(f"/audio/{OV_ID}/")
        sound = Sound.objects.get(source="openverse", external_id=OV_ID)
        self.assertEqual((post["sound"]["id"], post["sound"]["usable"]), (str(sound.pk), True))
        # A second user reuses it by its stored id, with no provider call.
        self.auth(self.make_user("ovfan"))
        with mock.patch.object(music, "_openverse") as api:
            again = self.assertOk(
                self.client.post(reverse("posts-list"), {"caption": "y", "sound_id": str(sound.pk)}), 201
            )
            api.assert_not_called()
        self.assertEqual(again["sound"]["id"], str(sound.pk))
        sound.refresh_from_db()
        self.assertEqual(sound.uses_count, 2)

    def test_openverse_track_with_disallowed_licence_is_refused(self):
        with mock.patch.object(music, "_openverse", return_value=openverse_track(license="by-nc-nd")):
            r = self.client.post(reverse("posts-list"), {"caption": "x", "sound_id": f"openverse:{OV_ID}"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Sound.objects.filter(source="openverse").exists())

    def test_invalid_openverse_id_is_refused_without_network(self):
        with mock.patch.object(music, "_openverse") as api:
            r = self.client.post(reverse("posts-list"), {"caption": "x", "sound_id": "openverse:../../etc"})
            api.assert_not_called()
        self.assertEqual(r.status_code, 400)

    def test_story_with_sound(self):
        asset = self.make_asset(self.user, purpose="story")
        with mock.patch.object(music, "_openverse", return_value=openverse_track()):
            story = self.assertOk(
                self.client.post(
                    reverse("stories-list"),
                    {"media_id": asset.pk, "sound_id": f"openverse:{OV_ID}", "sound_start": 12, "sound_volume": 0.8},
                ),
                201,
            )
        self.assertEqual(
            (story["sound"]["title"], story["sound"]["start"], story["sound"]["volume"]), ("Savanna Sunrise", 12.0, 0.8)
        )
        self.assertTrue(story["sound"]["plays_separately"])
        sound = Sound.objects.get(source="openverse")
        self.assertEqual(sound.uses_count, 1)
        self.assertEqual(self.client.delete(reverse("stories-detail", args=[story["id"]])).status_code, 204)
        sound.refresh_from_db()
        self.assertEqual(sound.uses_count, 0)

    def test_story_without_sound(self):
        asset = self.make_asset(self.user, purpose="story")
        story = self.assertOk(self.client.post(reverse("stories-list"), {"media_id": asset.pk}), 201)
        self.assertIsNone(story["sound"])


class ProxyThrottleIdentTests(FlashXTestCase):
    def _ident(self, **meta):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from apps.core.throttles import AuthRateThrottle

        request = Request(APIRequestFactory().post("/api/auth/login/", **meta))
        return AuthRateThrottle().get_ident(request)

    @override_settings(WEB_PROXY_SECRET="s3cret")
    def test_website_proxy_reports_visitor_ip(self):
        ident = self._ident(REMOTE_ADDR="10.0.0.1", HTTP_X_FLASHX_PROXY="s3cret", HTTP_X_FLASHX_CLIENT_IP="41.90.1.2")
        self.assertEqual(ident, "41.90.1.2")

    @override_settings(WEB_PROXY_SECRET="s3cret")
    def test_wrong_secret_is_ignored(self):
        ident = self._ident(REMOTE_ADDR="10.0.0.1", HTTP_X_FLASHX_PROXY="nope", HTTP_X_FLASHX_CLIENT_IP="41.90.1.2")
        self.assertEqual(ident, "10.0.0.1")

    @override_settings(WEB_PROXY_SECRET="")
    def test_disabled_without_secret(self):
        ident = self._ident(REMOTE_ADDR="10.0.0.1", HTTP_X_FLASHX_PROXY="", HTTP_X_FLASHX_CLIENT_IP="41.90.1.2")
        self.assertEqual(ident, "10.0.0.1")
