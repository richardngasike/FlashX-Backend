import re
from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.users.models import EmailVerificationCode, User
from apps.users.services import EMAIL_CODE_MAX_ATTEMPTS, purge_unconfirmed_accounts

from .base import FlashXTestCase


class EmailVerificationTests(FlashXTestCase):
    def register(self, email="new.person@example.com", username="newperson"):
        res = self.client.post(
            reverse("auth-register"),
            {
                "full_name": "New Person",
                "username": username,
                "email": email,
                "password": "Str0ng-pass-123",
                "confirm_password": "Str0ng-pass-123",
            },
            format="json",
        )
        data = self.assertOk(res, 201)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['tokens']['access']}")
        return data

    def last_code(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].subject).group(1)

    def verify(self, code):
        return self.client.post(reverse("auth-email-verify"), {"code": code}, format="json")

    def test_sign_up_sends_a_code_and_blocks_the_app_until_confirmed(self):
        data = self.register()
        self.assertFalse(data["user"]["email_verified"])
        self.assertEqual(mail.outbox[-1].to, ["new.person@example.com"])
        # Feed and everything else: refused with a clear code.
        self.assertError(self.client.get(reverse("posts-list")), 403, "email_unverified")
        # Own account still readable, so the app can show the code screen.
        self.assertOk(self.client.get(reverse("users-me")))
        user = self.assertOk(self.verify(self.last_code()))
        self.assertTrue(user["email_verified"])
        self.assertOk(self.client.get(reverse("posts-list")))

    def test_wrong_code_then_lockout(self):
        self.register()
        code = self.last_code()
        wrong = "000000" if code != "000000" else "111111"
        res = self.verify(wrong)
        self.assertError(res, 400, "validation_error")
        self.assertIn("Wrong code", str(res.json()["error"]["details"]["code"]))
        for _ in range(EMAIL_CODE_MAX_ATTEMPTS - 1):
            self.verify(wrong)
        self.assertIn("expired", str(self.verify(code).json()["error"]["details"]["code"]))
        # A new code works.
        EmailVerificationCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.assertOk(self.client.post(reverse("auth-email-resend")))
        self.assertOk(self.verify(self.last_code()))

    def test_resend_cooldown_and_old_code_stops_working(self):
        self.register()
        first = self.last_code()
        self.assertError(self.client.post(reverse("auth-email-resend")), 429, "throttled")
        EmailVerificationCode.objects.update(created_at=timezone.now() - timedelta(minutes=1))
        self.assertOk(self.client.post(reverse("auth-email-resend")))
        second = self.last_code()
        if first != second:
            self.assertError(self.verify(first), 400)
        self.assertOk(self.verify(second))

    def test_fix_a_mistyped_email(self):
        self.register(email="typo@exmaple.com")
        res = self.client.post(reverse("auth-email-change"), {"email": "right@example.com"}, format="json")
        self.assertEqual(self.assertOk(res)["email"], "right@example.com")
        self.assertEqual(mail.outbox[-1].to, ["right@example.com"])
        self.assertOk(self.verify(self.last_code()))
        # Once confirmed, the address can't be swapped this way.
        res = self.client.post(reverse("auth-email-change"), {"email": "other@example.com"}, format="json")
        self.assertError(res, 400)

    def test_cannot_take_an_existing_email(self):
        self.make_user("owner")
        self.register()
        res = self.client.post(reverse("auth-email-change"), {"email": "owner@example.com"}, format="json")
        self.assertError(res, 400, "validation_error")

    def test_unconfirmed_accounts_are_hidden_from_others(self):
        data = self.register()
        viewer = self.make_user("viewer")
        self.auth(viewer)
        res = self.client.get(reverse("users-detail", args=[data["user"]["id"]]))
        self.assertEqual(res.status_code, 404)

    def test_profile_edits_wait_for_confirmation(self):
        self.register()
        res = self.client.patch(reverse("users-me"), {"bio": "hello"}, format="json")
        self.assertError(res, 403, "email_unverified")

    def test_existing_accounts_count_as_confirmed(self):
        user = self.make_user("old")
        self.assertTrue(user.email_verified)

    def test_purge_removes_only_old_unconfirmed_accounts(self):
        self.register()
        fresh = self.make_user("fresh")
        User.objects.filter(username="newperson").update(date_joined=timezone.now() - timedelta(days=8))
        self.assertEqual(purge_unconfirmed_accounts(older_than_days=7), 1)
        self.assertFalse(User.objects.filter(username="newperson").exists())
        self.assertTrue(User.objects.filter(pk=fresh.pk).exists())
