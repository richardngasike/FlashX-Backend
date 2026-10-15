from unittest import mock

from django.db import OperationalError
from rest_framework.test import APITransactionTestCase

from apps.core import middleware as mw
from apps.users.models import User


class DatabaseFailureTests(APITransactionTestCase):
    """A dropped database connection must never leak details, and safe
    requests are retried once on a fresh connection."""

    def setUp(self):
        mw.DatabaseRetryMiddleware.delay_seconds = 0
        self.user = User.objects.create_user(username="ana", email="ana@example.com", password="Passw0rd!x")

    def test_unhandled_error_is_generic(self):
        with mock.patch("apps.core.views.HealthView.get", side_effect=RuntimeError("secret detail at db.internal")):
            res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 500)
        body = res.json()
        self.assertEqual(body["error"]["code"], "unavailable")
        self.assertNotIn("secret", str(body))
        self.assertNotIn("side", body["error"]["message"])

    def test_db_failure_is_retried_for_reads(self):
        calls = {"n": 0}
        real = __import__("apps.core.views", fromlist=["HealthView"]).HealthView.get

        def flaky(view, request, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("connection to server at 10.0.0.1 failed: max clients reached")
            return real(view, request, *a, **kw)

        with mock.patch("apps.core.views.HealthView.get", flaky):
            res = self.client.get("/api/health/")
        self.assertEqual(calls["n"], 2)
        self.assertIn(res.status_code, (200, 503))
        self.assertNotIn("10.0.0.1", res.content.decode())

    def test_db_failure_twice_returns_503_without_details(self):
        with mock.patch("apps.core.views.HealthView.get", side_effect=OperationalError("host db.internal down")):
            res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res["Retry-After"], "2")
        body = res.json()
        self.assertEqual(body["error"]["code"], "unavailable")
        self.assertNotIn("db.internal", str(body))

    def test_writes_are_not_retried(self):
        self.client.force_authenticate(self.user)
        with mock.patch("apps.notifications.views.MarkAllReadView.post", side_effect=OperationalError("down")) as m:
            res = self.client.post("/api/notifications/read-all/", format="json")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(m.call_count, 1)
