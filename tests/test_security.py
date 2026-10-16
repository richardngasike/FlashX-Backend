from django.core import mail
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.core.middleware import client_ip
from apps.core.throttles import AuthRateThrottle, PasswordResetThrottle, PaymentThrottle
from apps.users import services as user_services

from .base import PASSWORD, FlashXTestCase


class SecurityTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        caches["security"].clear()
        self.user = self.make_user("guarded")

    def login(self, password, identifier="guarded"):
        return self.client.post(reverse("auth-login"), {"identifier": identifier, "password": password}, format="json")

    # Sign-in protection ------------------------------------------------------
    def test_account_locks_after_repeated_wrong_passwords(self):
        for _ in range(user_services.LOGIN_MAX_FAILURES):
            self.assertError(self.login("wrong-password"), 401, "invalid_credentials")
        # Even the right password is refused while locked, for any spelling of the account.
        self.assertError(self.login(PASSWORD), 429, "throttled")
        self.assertError(self.login(PASSWORD, identifier="GUARDED@example.com"), 429, "throttled")
        self.assertError(self.login(PASSWORD, identifier="  Guarded "), 429, "throttled")

    def test_successful_sign_in_clears_failures(self):
        for _ in range(user_services.LOGIN_MAX_FAILURES - 1):
            self.login("wrong-password")
        self.assertOk(self.login(PASSWORD))
        self.assertError(self.login("wrong-password"), 401, "invalid_credentials")
        self.assertOk(self.login(PASSWORD))

    def test_password_length_is_bounded(self):
        self.assertError(self.login("x" * 129), 400, "validation_error")

    def test_password_change_ends_existing_sessions(self):
        tokens = self.assertOk(self.login(PASSWORD))["tokens"]
        old_access = tokens["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
        self.assertOk(self.client.get(reverse("users-me")))
        res = self.client.post(
            reverse("auth-password-change"),
            {"current_password": PASSWORD, "new_password": "An0ther-strong-pass"},
            format="json",
        )
        new_access = self.assertOk(res)["tokens"]["access"]
        # The access token issued before the change no longer works...
        self.assertEqual(self.client.get(reverse("users-me")).status_code, 401)
        # ...nor does the old refresh token; the new pair does.
        self.client.credentials()
        refresh = self.client.post(reverse("auth-refresh"), {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(refresh.status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access}")
        self.assertOk(self.client.get(reverse("users-me")))

    def test_reset_codes_are_capped_per_account(self):
        url = reverse("auth-password-reset")
        for _ in range(user_services.RESET_CODES_PER_HOUR + 2):
            self.assertOk(self.client.post(url, {"email": self.user.email}))
        self.assertEqual(len(mail.outbox), user_services.RESET_CODES_PER_HOUR)

    # Rate limits ---------------------------------------------------------------
    def test_security_throttles_use_the_shared_cache(self):
        from apps.core.cache import security_cache

        for throttle in (AuthRateThrottle(), PasswordResetThrottle(), PaymentThrottle()):
            self.assertIs(throttle.cache, security_cache)
        self.assertIs(security_cache.backend, caches["security"])

    def test_admin_login_attempts_are_limited(self):
        from apps.core.middleware import AdminLoginThrottleMiddleware

        url = "/admin/login/"
        for _ in range(AdminLoginThrottleMiddleware.attempts):
            self.assertNotEqual(self.client.post(url, {"username": "x", "password": "y"}).status_code, 429)
        self.assertEqual(self.client.post(url, {"username": "x", "password": "y"}).status_code, 429)

    def test_client_ip_ignores_forged_forwarded_hops(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR="1.2.3.4, 41.90.10.20")
        with override_settings(REST_FRAMEWORK={"NUM_PROXIES": 1}):
            self.assertEqual(client_ip(request), "41.90.10.20")
        with override_settings(REST_FRAMEWORK={}):
            self.assertEqual(client_ip(request), "10.0.0.9")

    # Responses -----------------------------------------------------------------
    def test_api_responses_carry_security_headers(self):
        self.auth(self.user)
        res = self.client.get(reverse("users-me"), HTTP_AUTHORIZATION="Bearer x")
        self.assertIn("default-src 'none'", res["Content-Security-Policy"])
        self.assertIn("camera=()", res["Permissions-Policy"])
        self.assertEqual(res["Cache-Control"], "no-store")

    def test_share_pages_have_a_strict_content_policy(self):
        post = self.make_post(self.user)
        csp = self.client.get(f"/p/{post.pk}/")["Content-Security-Policy"]
        # No scripts at all: default-src 'none' with no script-src exception.
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("script-src", csp)

    def test_public_health_check_hides_service_details(self):
        body = self.assertOk(self.client.get(reverse("health")))
        self.assertEqual(set(body), {"status", "database", "migrations_pending", "api_version"})

    # Resilience ---------------------------------------------------------------
    @override_settings(
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "security": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "not_created_yet"},
        }
    )
    def test_sign_in_works_before_the_cache_table_exists(self):
        self.assertOk(self.login(PASSWORD))
        self.assertError(self.login("wrong-password"), 401, "invalid_credentials")

    def test_sessions_from_before_the_update_end_cleanly(self):
        from unittest import mock

        from rest_framework_simplejwt.tokens import RefreshToken

        with mock.patch("rest_framework_simplejwt.settings.api_settings.CHECK_REVOKE_TOKEN", False):
            old = RefreshToken.for_user(self.user)
        res = self.client.post(reverse("auth-refresh"), {"refresh": str(old)}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_refresh_for_a_deleted_account_is_401(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = str(RefreshToken.for_user(self.user))
        self.user.delete()
        res = self.client.post(reverse("auth-refresh"), {"refresh": token}, format="json")
        self.assertEqual(res.status_code, 401)
