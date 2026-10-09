from django.urls import reverse

from apps.messaging.models import Conversation, Message

from .base import FlashXTestCase


class MessagingTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.auth(self.make_user("faith"))
        self.kelvin = self.make_user("kelvin")

    def send(self, **data):
        return self.client.post(reverse("messages-conversations"), data)

    def test_send_creates_single_direct_thread(self):
        m1 = self.assertOk(self.send(recipient_id=self.kelvin.id, content="Hey! How are you doing?"), 201)
        m2 = self.assertOk(self.send(recipient_id=self.kelvin.id, content="Again"), 201)
        self.assertEqual(m1["conversation_id"], m2["conversation_id"])
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(m1["status"], "sent")
        self.assertTrue(m1["is_mine"])

    def test_cannot_message_self_or_empty(self):
        self.assertError(self.send(recipient_id=self.me.id, content="me"), 400, "self_message")
        self.assertError(self.send(recipient_id=self.kelvin.id, content="  "), 400)

    def test_non_member_cannot_read_or_post(self):
        convo = self.assertOk(self.send(recipient_id=self.kelvin.id, content="private"), 201)["conversation_id"]
        self.auth(self.make_user("snoop"))
        self.assertError(self.client.get(reverse("messages-thread", args=[convo])), 404)
        self.assertError(self.send(conversation_id=convo, content="hi"), 404)

    def test_unread_counts_read_receipts_and_list(self):
        convo = self.assertOk(self.send(recipient_id=self.kelvin.id, content="one"), 201)["conversation_id"]
        self.send(conversation_id=convo, content="two")
        self.auth(self.kelvin)
        listing = self.assertOk(self.client.get(reverse("messages-conversations")))["results"]
        self.assertEqual(listing[0]["unread_count"], 2)
        self.assertEqual(listing[0]["last_message"]["text"], "two")
        self.assertEqual(listing[0]["title"], self.me.full_name)
        self.assertEqual(self.assertOk(self.client.get(reverse("messages-unread-count")))["unread_conversations"], 1)
        self.assertEqual(self.assertOk(self.client.post(reverse("messages-read", args=[convo])))["marked_read"], 2)
        self.assertEqual(
            self.assertOk(self.client.get(reverse("messages-conversations")))["results"][0]["unread_count"], 0
        )
        self.auth(self.me)
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[convo])))["results"]
        self.assertEqual([m["status"] for m in thread], ["read", "read"])

    def test_media_reply_and_delete(self):
        convo = self.assertOk(self.send(recipient_id=self.kelvin.id, content="first"), 201)
        asset = self.make_asset(self.me, "message")
        msg = self.assertOk(
            self.send(conversation_id=convo["conversation_id"], media_id=asset.id, reply_to_id=convo["id"]), 201
        )
        self.assertEqual(msg["media"]["type"], "image")
        self.assertEqual(msg["reply_to"]["id"], convo["id"])
        self.auth(self.kelvin)
        self.assertError(self.client.delete(reverse("messages-delete", args=[msg["id"]])), 403)
        self.auth(self.me)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertOk(self.client.delete(reverse("messages-delete", args=[msg["id"]])), 204)
        self.destroy.assert_called_once_with(asset.public_id, "image")
        deleted = Message.objects.get(pk=msg["id"])
        self.assertTrue(deleted.is_deleted)
        thread = self.assertOk(self.client.get(reverse("messages-thread", args=[convo["conversation_id"]])))["results"]
        self.assertIsNone(thread[0]["content"])
        self.assertIsNone(thread[0]["media"])

    def test_group_and_search_and_clear(self):
        lilian = self.make_user("lilian", full_name="Lilian A")
        convo = self.assertOk(
            self.client.post(
                reverse("messages-start"), {"participant_ids": [self.kelvin.id, lilian.id], "title": "Tech & Life"}
            ),
            201,
        )
        self.assertTrue(convo["is_group"])
        self.send(conversation_id=convo["id"], content="Hello group")
        self.assertEqual(
            len(self.assertOk(self.client.get(reverse("messages-conversations"), {"q": "lilian"}))["results"]), 1
        )
        self.assertError(self.client.post(reverse("messages-start"), {"participant_ids": [self.kelvin.id]}), 400)
        self.assertOk(self.client.delete(reverse("messages-thread", args=[convo["id"]])), 204)
        self.assertEqual(self.assertOk(self.client.get(reverse("messages-conversations")))["results"], [])

    def test_mute(self):
        convo = self.assertOk(self.send(recipient_id=self.kelvin.id, content="x"), 201)["conversation_id"]
        self.auth(self.kelvin)
        self.assertTrue(
            self.assertOk(self.client.post(reverse("messages-mute", args=[convo]), {"muted": True}))["is_muted"]
        )
        from apps.notifications.models import Notification

        before = Notification.objects.filter(recipient=self.kelvin).count()
        self.auth(self.me)
        self.send(conversation_id=convo, content="muted?")
        self.assertEqual(Notification.objects.filter(recipient=self.kelvin).count(), before)


class GroupManagementTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        from apps.messaging.services import create_group

        self.admin = self.make_user("group_admin")
        self.a = self.make_user("member_a")
        self.b = self.make_user("member_b")
        self.convo = create_group(self.admin, [self.a.pk, self.b.pk], "Weekend")

    def members(self, user):
        self.auth(user)
        return self.assertOk(self.client.get(reverse("messages-members", args=[self.convo.pk])))

    def test_members_list_admin_first_and_flags(self):
        data = self.members(self.a)
        self.assertEqual(data["admin_id"], self.admin.pk)
        self.assertEqual(data["results"][0]["id"], self.admin.pk)
        self.assertTrue(data["results"][0]["is_admin"])
        self.assertEqual(len(data["results"]), 3)
        info = self.assertOk(self.client.get(reverse("messages-info", args=[self.convo.pk])))
        self.assertEqual((info["member_count"], info["is_admin"], info["admin_id"]), (3, False, self.admin.pk))

    def test_member_leaves(self):
        self.auth(self.a)
        self.assertOk(self.client.post(reverse("messages-leave", args=[self.convo.pk])), 204)
        self.assertError(self.client.get(reverse("messages-info", args=[self.convo.pk])), 404)
        self.assertEqual(len(self.members(self.b)["results"]), 2)

    def test_admin_leaving_hands_over_admin(self):
        self.auth(self.admin)
        self.client.post(reverse("messages-leave", args=[self.convo.pk]))
        self.convo.refresh_from_db()
        self.assertIn(self.convo.created_by_id, {self.a.pk, self.b.pk})

    def test_last_member_leaving_deletes_group(self):
        from apps.messaging.models import Conversation

        for user in (self.admin, self.a, self.b):
            self.auth(user)
            self.client.post(reverse("messages-leave", args=[self.convo.pk]))
        self.assertFalse(Conversation.objects.filter(pk=self.convo.pk).exists())

    def test_admin_adds_and_removes(self):
        c = self.make_user("member_c")
        self.auth(self.admin)
        added = self.assertOk(
            self.client.post(
                reverse("messages-members", args=[self.convo.pk]), {"user_ids": [c.pk, self.a.pk]}, format="json"
            ),
            201,
        )
        self.assertEqual(added["added"], [c.pk])
        self.assertOk(self.client.delete(reverse("messages-member-detail", args=[self.convo.pk, c.pk])), 204)
        self.assertEqual(len(self.members(self.admin)["results"]), 3)

    def test_non_admin_cannot_add_or_remove(self):
        c = self.make_user()
        self.auth(self.a)
        self.assertError(
            self.client.post(reverse("messages-members", args=[self.convo.pk]), {"user_ids": [c.pk]}, format="json"),
            403,
        )
        self.assertError(self.client.delete(reverse("messages-member-detail", args=[self.convo.pk, self.b.pk])), 403)

    def test_cannot_leave_direct_thread(self):
        from apps.messaging.services import get_or_create_direct

        direct = get_or_create_direct(self.a, self.b)
        self.auth(self.a)
        self.assertError(self.client.post(reverse("messages-leave", args=[direct.pk])), 400, "not_a_group")
