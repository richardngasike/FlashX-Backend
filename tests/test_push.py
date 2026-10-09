import base64
import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from apps.likes.services import like
from apps.messaging.services import get_or_create_direct, send_message, set_muted
from apps.notifications import push
from apps.notifications.models import DeviceToken

from .base import FlashXTestCase


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class DeviceTokenEndpointTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.auth(self.make_user())
        self.url = reverse("notifications-devices")

    def test_register_update_and_delete(self):
        self.assertOk(self.client.post(self.url, {"token": "tok-1", "platform": "android"}), 201)
        self.assertOk(self.client.post(self.url, {"token": "tok-1", "platform": "android", "app_version": "1.0.1"}))
        device = DeviceToken.objects.get()
        self.assertEqual((device.user, device.app_version), (self.user, "1.0.1"))
        self.assertOk(self.client.delete(self.url, {"token": "tok-1"}, format="json"), 204)
        self.assertFalse(DeviceToken.objects.exists())

    def test_token_moves_to_latest_user(self):
        self.client.post(self.url, {"token": "shared-phone", "platform": "android"})
        other = self.auth(self.make_user())
        self.client.post(self.url, {"token": "shared-phone", "platform": "android"})
        self.assertEqual(DeviceToken.objects.get().user, other)

    def test_validation(self):
        self.assertError(self.client.post(self.url, {"token": "x", "platform": "windows"}), 400, "validation_error")
        self.client.force_authenticate(None)
        self.assertError(self.client.post(self.url, {"token": "x", "platform": "android"}), 401)


class PushDispatchTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.make_user("alice", full_name="Alice Achieng")
        self.bob = self.make_user("bob", full_name="Bob Otieno")
        DeviceToken.objects.create(user=self.alice, token="alice-phone", platform="android")
        mock.patch.object(push, "is_enabled", return_value=True).start()
        mock.patch.object(push, "_access_token", return_value=("access", "flashx-proj")).start()
        self.post = mock.patch("requests.post", return_value=FakeResponse()).start()

    def sent_messages(self):
        return [c.kwargs["json"]["message"] for c in self.post.call_args_list]

    def test_activity_notification_is_pushed_after_commit(self):
        post = self.make_post(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            like(self.bob, post)
        (msg,) = self.sent_messages()
        self.assertEqual(self.post.call_args.args[0], push.SEND_URL.format(project="flashx-proj"))
        self.assertEqual(msg["token"], "alice-phone")
        self.assertEqual(msg["notification"], {"title": "Bob Otieno", "body": "liked your post."})
        self.assertEqual(msg["android"]["notification"]["channel_id"], push.CHANNEL_ACTIVITY)
        self.assertEqual(msg["android"]["notification"]["sound"], push.SOUND_ANDROID)
        self.assertEqual(msg["apns"]["payload"]["aps"]["sound"], push.SOUND_IOS)
        self.assertEqual(msg["data"]["type"], "like")
        self.assertEqual(msg["data"]["reference_id"], str(post.pk))
        self.assertEqual(msg["data"]["sender_username"], "bob")
        self.assertTrue(all(isinstance(v, str) for v in msg["data"].values()))

    def test_message_uses_message_channel_and_high_priority(self):
        convo = get_or_create_direct(self.bob, self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.bob, convo, content="Habari?")
        (msg,) = self.sent_messages()
        self.assertEqual(msg["notification"], {"title": "Bob Otieno", "body": "Habari?"})
        self.assertEqual(msg["android"]["priority"], "HIGH")
        self.assertEqual(msg["android"]["notification"]["channel_id"], push.CHANNEL_MESSAGES)
        self.assertEqual(msg["data"]["target_type"], "conversation")
        self.assertEqual(msg["data"]["reference_id"], str(convo.pk))

    def test_muted_conversation_is_not_pushed(self):
        convo = get_or_create_direct(self.bob, self.alice)
        set_muted(self.alice, convo.pk, True)
        with self.captureOnCommitCallbacks(execute=True):
            send_message(self.bob, convo, content="quiet")
        self.post.assert_not_called()

    def test_dead_token_is_removed(self):
        self.post.return_value = FakeResponse(404, {"error": {"status": "NOT_FOUND"}})
        with self.captureOnCommitCallbacks(execute=True):
            like(self.bob, self.make_post(self.alice))
        self.assertFalse(DeviceToken.objects.exists())

    def test_unregistered_token_is_removed(self):
        body = {"error": {"status": "INVALID_ARGUMENT", "details": [{"errorCode": "UNREGISTERED"}]}}
        self.post.return_value = FakeResponse(400, body)
        with self.captureOnCommitCallbacks(execute=True):
            like(self.bob, self.make_post(self.alice))
        self.assertFalse(DeviceToken.objects.exists())

    def test_network_failure_never_breaks_the_request(self):
        import requests

        self.post.side_effect = requests.ConnectionError("down")
        with self.captureOnCommitCallbacks(execute=True):
            like(self.bob, self.make_post(self.alice))
        self.assertTrue(DeviceToken.objects.exists())

    def test_rolled_back_action_is_not_pushed(self):
        from django.db import transaction

        post = self.make_post(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    like(self.bob, post)
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
        self.post.assert_not_called()


class PushConfigTests(FlashXTestCase):
    KEY = {"type": "service_account", "project_id": "flashx-proj", "private_key": "k", "client_email": "a@b"}

    def setUp(self):
        super().setUp()
        push._reset_credentials()
        self.addCleanup(push._reset_credentials)

    def test_disabled_without_key(self):
        self.assertFalse(push.is_enabled())
        self.assertEqual(push.send_to_user(1, title="t", body="b", data={}, channel="c"), 0)

    def test_accepts_raw_and_base64_json(self):
        raw = json.dumps(self.KEY)
        with override_settings(FCM={"SERVICE_ACCOUNT_JSON": raw}):
            self.assertEqual(push._service_account_info()["project_id"], "flashx-proj")
        encoded = base64.b64encode(raw.encode()).decode()
        with override_settings(FCM={"SERVICE_ACCOUNT_JSON": encoded}):
            self.assertEqual(push._service_account_info()["project_id"], "flashx-proj")

    def test_bad_key_disables_push_without_raising(self):
        user = self.make_user()
        DeviceToken.objects.create(user=user, token="t", platform="android")
        with (
            override_settings(FCM={"SERVICE_ACCOUNT_JSON": "not-a-key"}),
            mock.patch("requests.post") as post,
            self.assertLogs("apps.notifications.push", level="ERROR"),
        ):
            self.assertEqual(push.send_to_user(user.pk, title="t", body="b", data={}, channel="c"), 0)
            post.assert_not_called()
