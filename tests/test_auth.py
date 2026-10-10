import re

from django.core import mail
from django.urls import reverse

from apps.users.models import User

from .base import PASSWORD, FlashXTestCase


class RegistrationTests(FlashXTestCase):
    url = reverse("auth-register")

    def payload(self, **kw):
        data = {
            "full_name": "Faith Wanjiku",
            "username": "faith_w",
            "email": "Faith@Example.com",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        }
        data.update(kw)
        return data

    def test_register_returns_user_and_tokens(self):
        data = self.assertOk(self.client.post(self.url, self.payload()), 201)
        self.assertEqual(data["user"]["username"], "faith_w")
        self.assertEqual(data["user"]["email"], "faith@example.com")
        self.assertIn("access", data["tokens"])
        user = User.objects.get(username="faith_w")
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_username_unique_case_insensitive(self):
        self.make_user("Faith_W")
        err = self.assertError(self.client.post(self.url, self.payload()), 400, "validation_error")
        self.assertIn("username", err["details"])

    def test_email_unique_case_insensitive(self):
        self.client.post(self.url, self.payload())
        err = self.assertError(self.client.post(self.url, self.payload(username="other")), 400)
        self.assertIn("email", err["details"])

    def test_password_mismatch_and_weak_password(self):
        err = self.assertError(self.client.post(self.url, self.payload(confirm_password="nope-nope-1")), 400)
        self.assertIn("confirm_password", err["details"])
        self.assertError(
            self.client.post(self.url, self.payload(password="12345678", confirm_password="12345678")), 400
        )

    def test_invalid_and_reserved_usernames(self):
        for bad in ("ab", "has space", ".dot", "dot.", "a..b", "admin", "12345"):
            self.assertError(self.client.post(self.url, self.payload(username=bad)), 400)

    def test_username_availability(self):
        self.make_user("taken_name")
        url = reverse("auth-username-available")
        self.assertFalse(self.assertOk(self.client.get(url, {"username": "TAKEN_name"}))["available"])
        self.assertTrue(self.assertOk(self.client.get(url, {"username": "free_name"}))["available"])
        self.assertFalse(self.assertOk(self.client.get(url, {"username": "x"}))["available"])


class LoginTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("brian254")

    def test_login_with_username_or_email(self):
        for ident in ("brian254", "BRIAN254", "brian254@example.com"):
            data = self.assertOk(self.client.post(reverse("auth-login"), {"identifier": ident, "password": PASSWORD}))
            self.assertEqual(data["user"]["id"], self.user.id)

    def test_invalid_credentials(self):
        self.assertError(
            self.client.post(reverse("auth-login"), {"identifier": "brian254", "password": "wrong-pass-1"}),
            401,
            "invalid_credentials",
        )
        self.assertError(
            self.client.post(reverse("auth-login"), {"identifier": "ghost", "password": "wrong-pass-1"}), 401
        )

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        self.assertError(self.client.post(reverse("auth-login"), {"identifier": "brian254", "password": PASSWORD}), 401)

    def test_refresh_rotates_and_logout_blacklists(self):
        tokens = self.assertOk(
            self.client.post(reverse("auth-login"), {"identifier": "brian254", "password": PASSWORD})
        )["tokens"]
        new = self.assertOk(self.client.post(reverse("auth-refresh"), {"refresh": tokens["refresh"]}))
        self.assertIn("refresh", new)
        # Old refresh token is blacklisted after rotation.
        self.assertError(self.client.post(reverse("auth-refresh"), {"refresh": tokens["refresh"]}), 401)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {new['access']}")
        self.assertOk(self.client.post(reverse("auth-logout"), {"refresh": new["refresh"]}), 204)
        self.assertError(self.client.post(reverse("auth-refresh"), {"refresh": new["refresh"]}), 401)

    def test_protected_endpoint_requires_token(self):
        self.assertError(self.client.get(reverse("users-me")), 401, "not_authenticated")

    def test_jwt_request_updates_presence(self):
        tokens = self.assertOk(
            self.client.post(reverse("auth-login"), {"identifier": "brian254", "password": PASSWORD})
        )["tokens"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        data = self.assertOk(self.client.get(reverse("users-me")))
        self.assertTrue(data["is_online"])


class PasswordTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("lilian")

    def test_reset_flow(self):
        self.assertOk(self.client.post(reverse("auth-password-reset"), {"email": "lilian@example.com"}))
        self.assertEqual(len(mail.outbox), 1)
        uid, token = re.search(r"uid=([^&\s]+)&token=([^\s]+)", mail.outbox[0].body).groups()
        self.assertOk(
            self.client.post(
                reverse("auth-password-reset-confirm"), {"uid": uid, "token": token, "new_password": "Brand-new-pass-9"}
            )
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-new-pass-9"))
        # Token is single-use.
        self.assertError(
            self.client.post(
                reverse("auth-password-reset-confirm"), {"uid": uid, "token": token, "new_password": "Another-pass-77"}
            ),
            400,
        )

    def test_reset_does_not_leak_accounts(self):
        self.assertOk(self.client.post(reverse("auth-password-reset"), {"email": "nobody@example.com"}))
        self.assertEqual(len(mail.outbox), 0)

    def test_email_delivery_flag(self):
        from django.test import override_settings

        from apps.users.services import email_delivery_enabled

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            self.assertFalse(email_delivery_enabled())
            self.assertFalse(self.assertOk(self.client.get(reverse("health")))["features"]["email"])
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"):
            self.assertTrue(email_delivery_enabled())

    def test_change_password(self):
        self.auth(self.user)
        self.assertError(
            self.client.post(
                reverse("auth-password-change"), {"current_password": "bad", "new_password": "Brand-new-pass-9"}
            ),
            400,
        )
        data = self.assertOk(
            self.client.post(
                reverse("auth-password-change"), {"current_password": PASSWORD, "new_password": "Brand-new-pass-9"}
            )
        )
        self.assertIn("access", data["tokens"])
