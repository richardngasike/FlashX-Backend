from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.messaging import services
from apps.messaging.models import Conversation, ConversationParticipant, Message, MessageReaction

from .base import FlashXTestCase


class RichMessagingTests(FlashXTestCase):
    """Attachments, GIFs, retries, edit, forward, reactions, pins, list state, typing, delivery, invites."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.me = self.auth(self.make_user("faith"))
        self.kelvin = self.make_user("kelvin")
        self.lilian = self.make_user("lilian")

    def send(self, **data):
        return self.client.post(reverse("messages-conversations"), data, format="json")

    def direct(self, text="hi"):
        return self.assertOk(self.send(recipient_id=self.kelvin.id, content=text), 201)

    def group(self):
        return self.assertOk(
            self.client.post(
                reverse("messages-start"),
                {"participant_ids": [self.kelvin.id, self.lilian.id], "title": "Team"},
                format="json",
            ),
            201,
        )

    # -- attachments --------------------------------------------------------
    def test_multiple_attachments_with_documents_and_audio(self):
        first = self.direct()
        photo = self.make_asset(self.me, "message")
        video = self.make_asset(self.me, "message", resource_type="video", format="mp4")
        voice = self.make_asset(self.me, "message", resource_type="video", format="m4a")
        doc = self.make_asset(self.me, "message", resource_type="raw", format="pdf")
        msg = self.assertOk(
            self.send(
                conversation_id=first["conversation_id"],
                media_ids=[photo.id, video.id, voice.id, doc.id],
                attachment_names={str(doc.id): "Quarterly report.pdf"},
            ),
            201,
        )
        kinds = [a["kind"] for a in msg["attachments"]]
        self.assertEqual(kinds, ["image", "video", "audio", "file"])
        self.assertEqual(msg["attachments"][3]["name"], "Quarterly report.pdf")
        self.assertIn("/raw/upload/", msg["attachments"][3]["url"])
        self.assertIn("mp3", msg["attachments"][2])
        # Older apps still see the first photo in "media".
        self.assertEqual(msg["media"]["type"], "image")
        self.auth(self.kelvin)
        listing = self.assertOk(self.client.get(reverse("messages-conversations")))["results"]
        self.assertEqual(listing[0]["last_message"]["text"], "4 attachments")

    def test_attachment_limits_and_ownership(self):
        first = self.direct()
        other = self.make_asset(self.kelvin, "message")
        self.assertError(self.send(conversation_id=first["conversation_id"], media_ids=[other.id]), 400)
        many = [self.make_asset(self.me, "message").id for _ in range(11)]
        self.assertError(self.send(conversation_id=first["conversation_id"], media_ids=many), 400)

    def test_sign_raw_upload_checks_extension(self):
        with mock.patch("apps.media.cloudinary_service.is_configured", return_value=True):
            ok = self.client.post(
                reverse("media-sign"),
                {"purpose": "message", "resource_type": "raw", "filename": "notes.docx"},
                format="json",
            )
            self.assertOk(ok)
            self.assertTrue(ok.json()["data"]["public_id"].endswith(".docx"))
            bad = self.client.post(
                reverse("media-sign"),
                {"purpose": "message", "resource_type": "raw", "filename": "virus.exe"},
                format="json",
            )
            self.assertError(bad, 415, "unsupported_file_type")

    def test_gif_messages_only_from_gif_cdns(self):
        first = self.direct()
        cid = first["conversation_id"]
        msg = self.assertOk(self.send(conversation_id=cid, gif_url="https://media.giphy.com/media/abc/giphy.gif"), 201)
        self.assertEqual(msg["gif_url"], "https://media.giphy.com/media/abc/giphy.gif")
        self.assertError(self.send(conversation_id=cid, gif_url="https://evil.example.com/x.gif"), 400, "invalid_gif")
        self.assertError(
            self.send(conversation_id=cid, gif_url="https://storage.googleapis.com/x.gif"), 400, "invalid_gif"
        )

    def test_gif_search_disabled_without_key(self):
        with self.settings(GIFS={"GIPHY_API_KEY": "", "TENOR_API_KEY": ""}):
            data = self.assertOk(self.client.get(reverse("messages-gifs"), {"q": "cat"}))
        self.assertFalse(data["enabled"])
        self.assertEqual(data["results"], [])

    def test_gif_search_giphy(self):
        payload = {
            "data": [
                {
                    "id": "g1",
                    "images": {
                        "downsized": {
                            "url": "https://media1.giphy.com/media/g1/giphy.gif?cid=x",
                            "width": "200",
                            "height": "100",
                        },
                        "fixed_width": {"url": "https://media1.giphy.com/media/g1/200w.gif?cid=x"},
                    },
                }
            ],
            "pagination": {"total_count": 40},
        }
        resp = mock.Mock(status_code=200)
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with self.settings(GIFS={"GIPHY_API_KEY": "k", "TENOR_API_KEY": "", "RATING": "pg"}):
            with mock.patch("requests.get", return_value=resp) as get:
                data = self.assertOk(self.client.get(reverse("messages-gifs"), {"q": "cat", "type": "stickers"}))
        self.assertIn("/v1/stickers/search", get.call_args[0][0])
        self.assertTrue(data["enabled"])
        self.assertEqual(data["results"][0]["url"], "https://media1.giphy.com/media/g1/giphy.gif")
        self.assertEqual(data["next_offset"], 1)

    # -- reliability --------------------------------------------------------
    def test_client_id_makes_retries_idempotent(self):
        first = self.direct()
        a = self.assertOk(self.send(conversation_id=first["conversation_id"], content="once", client_id="dev-1"), 201)
        b = self.assertOk(self.send(conversation_id=first["conversation_id"], content="once", client_id="dev-1"), 201)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["client_id"], "dev-1")
        self.assertEqual(Message.objects.filter(content="once").count(), 1)

    def test_delivered_then_read_status(self):
        first = self.direct("hello")
        cid = first["conversation_id"]
        self.assertEqual(first["status"], "sent")
        self.auth(self.kelvin)
        self.assertOk(self.client.get(reverse("messages-conversations")))
        self.auth(self.me)
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[cid])))["results"]
        self.assertEqual(thread[0]["status"], "delivered")
        self.auth(self.kelvin)
        self.assertOk(self.client.post(reverse("messages-read", args=[cid])))
        self.auth(self.me)
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[cid])))["results"]
        self.assertEqual(thread[0]["status"], "read")

    # -- edit / delete / forward -------------------------------------------
    def test_edit_within_window_by_sender_only(self):
        msg = self.direct("typo")
        url = reverse("messages-delete", args=[msg["id"]])
        edited = self.assertOk(self.client.patch(url, {"content": "fixed"}, format="json"))
        self.assertEqual(edited["content"], "fixed")
        self.assertTrue(edited["is_edited"])
        self.auth(self.kelvin)
        self.assertError(self.client.patch(url, {"content": "hack"}, format="json"), 403)
        self.auth(self.me)
        Message.objects.filter(pk=msg["id"]).update(created_at=timezone.now() - timedelta(minutes=20))
        self.assertError(self.client.patch(url, {"content": "late"}, format="json"), 400, "edit_window_closed")

    def test_forward_copies_files_and_keeps_them_after_original_deleted(self):
        first = self.direct()
        doc = self.make_asset(self.me, "message", resource_type="raw", format="pdf")
        msg = self.assertOk(self.send(conversation_id=first["conversation_id"], media_ids=[doc.id]), 201)
        out = self.assertOk(
            self.client.post(
                reverse("messages-forward", args=[msg["id"]]), {"recipient_ids": [self.lilian.id]}, format="json"
            ),
            201,
        )
        copy = Message.objects.get(pk=out["forwarded"][0]["id"])
        self.assertTrue(copy.forwarded)
        self.assertEqual(copy.attachments.get().public_id, doc.public_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.delete(reverse("messages-delete", args=[msg["id"]])), 204)
        # The forwarded copy still needs the file.
        self.destroy.assert_not_called()

    def test_bulk_delete(self):
        a = self.direct("one")
        b = self.direct("two")
        self.auth(self.kelvin)
        self.assertError(
            self.client.post(
                reverse("messages-bulk-delete"), {"ids": [a["id"], b["id"]], "scope": "everyone"}, format="json"
            ),
            403,
        )
        self.assertOk(
            self.client.post(reverse("messages-bulk-delete"), {"ids": [a["id"], b["id"]], "scope": "me"}, format="json")
        )
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[a["conversation_id"]])))["results"]
        self.assertEqual(thread, [])

    # -- reactions & pins ---------------------------------------------------
    def test_reactions_change_remove_and_list(self):
        msg = self.direct()
        url = reverse("messages-react", args=[msg["id"]])
        self.assertOk(self.client.post(url, {"emoji": "\U0001f602"}, format="json"))
        self.auth(self.kelvin)
        self.assertOk(self.client.post(url, {"emoji": "\U0001f602"}, format="json"))
        data = self.assertOk(self.client.post(url, {"emoji": "❤️"}, format="json"))
        self.assertEqual(MessageReaction.objects.filter(message_id=msg["id"]).count(), 2)
        summary = {r["emoji"]: r for r in data["reactions"]}
        self.assertTrue(summary["❤️"]["mine"])
        self.assertEqual(summary["\U0001f602"]["count"], 1)
        who = self.assertOk(self.client.get(url))["results"]
        self.assertEqual(len(who), 2)
        data = self.assertOk(self.client.delete(url))
        self.assertEqual([r["emoji"] for r in data["reactions"]], ["\U0001f602"])
        self.auth(self.make_user("outsider"))
        self.assertError(self.client.post(url, {"emoji": "x"}, format="json"), 404)

    def test_pin_messages_admin_only_in_groups(self):
        g = self.group()
        msg = self.assertOk(self.send(conversation_id=g["id"], content="rules"), 201)
        self.auth(self.kelvin)
        self.assertError(
            self.client.post(reverse("messages-pin", args=[msg["id"]]), {"pinned": True}, format="json"), 403
        )
        self.auth(self.me)
        pinned = self.assertOk(
            self.client.post(reverse("messages-pin", args=[msg["id"]]), {"pinned": True}, format="json")
        )
        self.assertTrue(pinned["is_pinned"])
        listing = self.assertOk(self.client.get(reverse("messages-pinned", args=[g["id"]])))["results"]
        self.assertEqual([m["id"] for m in listing], [msg["id"]])

    # -- list state ---------------------------------------------------------
    def test_pin_archive_and_mark_unread(self):
        a = self.direct("a")
        b = self.assertOk(self.send(recipient_id=self.lilian.id, content="b"), 201)
        state = reverse("messages-state", args=[a["conversation_id"]])
        data = self.assertOk(self.client.post(state, {"pinned": True}, format="json"))
        self.assertTrue(data["is_pinned"])
        listing = self.assertOk(self.client.get(reverse("messages-conversations")))
        self.assertEqual(listing["results"][0]["id"], a["conversation_id"])
        self.assertEqual(len(listing["results"]), 2)
        self.assertOk(
            self.client.post(reverse("messages-state", args=[b["conversation_id"]]), {"archived": True}, format="json")
        )
        listing = self.assertOk(self.client.get(reverse("messages-conversations")))
        self.assertEqual([c["id"] for c in listing["results"]], [a["conversation_id"]])
        self.assertEqual(listing["archived_count"], 1)
        archived = self.assertOk(self.client.get(reverse("messages-conversations"), {"archived": "1"}))["results"]
        self.assertEqual([c["id"] for c in archived], [b["conversation_id"]])
        data = self.assertOk(self.client.post(state, {"unread": True}, format="json"))
        self.assertTrue(data["marked_unread"])
        self.assertEqual(data["unread_count"], 1)
        self.assertOk(self.client.post(reverse("messages-read", args=[a["conversation_id"]])))
        self.assertFalse(
            ConversationParticipant.objects.get(user=self.me, conversation_id=a["conversation_id"]).marked_unread
        )

    # -- typing -------------------------------------------------------------
    def test_typing_indicator_and_clear_on_send(self):
        first = self.direct()
        cid = first["conversation_id"]
        self.assertOk(self.client.post(reverse("messages-typing", args=[cid]), {"typing": True}, format="json"))
        self.auth(self.kelvin)
        users = self.assertOk(self.client.get(reverse("messages-typing", args=[cid])))["users"]
        self.assertEqual([u["id"] for u in users], [self.me.id])
        self.auth(self.me)
        self.assertOk(self.send(conversation_id=cid, content="done"), 201)
        self.auth(self.kelvin)
        self.assertEqual(self.assertOk(self.client.get(reverse("messages-typing", args=[cid])))["users"], [])
        self.auth(self.make_user("outsider"))
        self.assertError(self.client.get(reverse("messages-typing", args=[cid])), 404)

    # -- search -------------------------------------------------------------
    def test_search_in_thread(self):
        first = self.direct("Meeting at noon")
        self.direct("unrelated")
        found = self.assertOk(
            self.client.get(reverse("messages-thread", args=[first["conversation_id"]]), {"q": "noon"})
        )["results"]
        self.assertEqual([m["id"] for m in found], [first["id"]])

    # -- admins & invite links ---------------------------------------------
    def test_admins_and_invite_links(self):
        g = self.group()
        gid = g["id"]
        self.auth(self.kelvin)
        self.assertError(self.client.get(reverse("messages-invite", args=[gid])), 403)
        self.auth(self.me)
        self.assertOk(
            self.client.patch(
                reverse("messages-member-detail", args=[gid, self.kelvin.id]), {"is_admin": True}, format="json"
            )
        )
        members = self.assertOk(self.client.get(reverse("messages-members", args=[gid])))["results"]
        self.assertTrue(next(m for m in members if m["id"] == self.kelvin.id)["is_admin"])
        self.auth(self.kelvin)
        code = self.assertOk(self.client.get(reverse("messages-invite", args=[gid])))["code"]
        info = self.assertOk(self.client.get(reverse("messages-info", args=[gid])))
        self.assertEqual(info["invite_code"], code)
        self.assertTrue(info["is_admin"])

        newbie = self.auth(self.make_user("newbie"))
        preview = self.assertOk(self.client.get(reverse("messages-join", args=[code])))
        self.assertEqual(preview["member_count"], 3)
        self.assertFalse(preview["is_member"])
        joined = self.assertOk(self.client.post(reverse("messages-join", args=[code])), 201)
        self.assertEqual(joined["id"], gid)
        self.assertIsNone(joined["invite_code"])
        self.assertTrue(ConversationParticipant.objects.filter(conversation_id=gid, user=newbie).exists())
        self.assertTrue(Message.objects.filter(conversation_id=gid, meta__event="joined").exists())

        self.auth(self.me)
        new_code = self.assertOk(self.client.post(reverse("messages-invite", args=[gid])))["code"]
        self.assertNotEqual(code, new_code)
        self.auth(self.make_user("late"))
        self.assertError(self.client.post(reverse("messages-join", args=[code])), 404, "invalid_invite")
        self.auth(self.me)
        self.assertOk(self.client.delete(reverse("messages-invite", args=[gid])), 204)
        self.assertIsNone(Conversation.objects.get(pk=gid).invite_code)

    def test_blocked_user_cannot_join_by_link(self):
        from apps.blocks.models import Block

        g = self.group()
        code = services.invite_link(self.me, g["id"])
        villain = self.make_user("villain")
        Block.objects.create(blocker=self.me, blocked=villain)
        self.auth(villain)
        self.assertError(self.client.post(reverse("messages-join", args=[code])), 404, "invalid_invite")
