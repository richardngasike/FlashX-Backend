import re
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import Ad
from apps.users.models import PasswordResetCode

from .base import PASSWORD, FlashXTestCase


class AdTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.auth(self.make_user())
        now = timezone.now()
        self.ad = Ad.objects.create(
            advertiser_name="Java House",
            headline="Coffee on us",
            image_url="https://res.cloudinary.com/x/ad.jpg",
            link_url="https://example.com/offer",
        )
        Ad.objects.create(advertiser_name="Paused", headline="x", link_url="https://example.com", is_active=False)
        Ad.objects.create(
            advertiser_name="Over", headline="x", link_url="https://example.com", ends_at=now - timedelta(days=1)
        )
        Ad.objects.create(
            advertiser_name="Soon", headline="x", link_url="https://example.com", starts_at=now + timedelta(days=1)
        )

    def test_only_running_ads_are_served(self):
        data = self.assertOk(self.client.get(reverse("ads-feed"), {"count": 5}))
        self.assertEqual([a["advertiser_name"] for a in data["results"]], ["Java House"])

    def test_impression_and_click_are_counted(self):
        self.assertOk(self.client.post(reverse("ads-impression", args=[self.ad.pk])))
        data = self.assertOk(self.client.post(reverse("ads-click", args=[self.ad.pk])))
        self.assertEqual(data["link_url"], "https://example.com/offer")
        self.ad.refresh_from_db()
        self.assertEqual((self.ad.impressions, self.ad.clicks), (1, 1))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetCodeTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("resetme")

    def request_code(self):
        self.client.post(reverse("auth-password-reset"), {"email": "RESETME@example.com"})
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].subject).group(1)

    def test_code_resets_password_once(self):
        code = self.request_code()
        url = reverse("auth-password-reset-code")
        payload = {"email": "resetme@example.com", "code": code, "new_password": "N3w-strong-pass!"}
        self.assertOk(self.client.post(url, payload))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3w-strong-pass!"))
        self.assertError(self.client.post(url, payload), 400)

    def test_wrong_code_locks_after_attempts(self):
        code = self.request_code()
        wrong = "000000" if code != "000000" else "111111"
        url = reverse("auth-password-reset-code")
        for _ in range(5):
            self.client.post(url, {"email": "resetme@example.com", "code": wrong, "new_password": "N3w-strong-pass!"})
        self.assertError(
            self.client.post(url, {"email": "resetme@example.com", "code": code, "new_password": "N3w-strong-pass!"}),
            400,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_expired_code_fails(self):
        code = self.request_code()
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertError(
            self.client.post(
                reverse("auth-password-reset-code"),
                {"email": "resetme@example.com", "code": code, "new_password": "N3w-strong-pass!"},
            ),
            400,
        )

    def test_unknown_email_sends_nothing(self):
        self.client.post(reverse("auth-password-reset"), {"email": "nobody@example.com"})
        self.assertEqual(len(mail.outbox), 0)
