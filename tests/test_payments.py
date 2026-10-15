from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.payments import mpesa
from apps.payments.models import MpesaPayment
from apps.wallet.models import WalletTransaction

from .base import FlashXTestCase

MPESA = {
    "ENVIRONMENT": "sandbox",
    "CONSUMER_KEY": "key",
    "CONSUMER_SECRET": "secret",
    "SHORTCODE": "174379",
    "PASSKEY": "passkey",
    "TILL_NUMBER": "",
    "CALLBACK_BASE_URL": "https://api.example.com",
    "CALLBACK_TOKEN": "cb-token-123",
}
PAYMENTS = {"DIRECT_ENABLED": True, "VERIFICATION_PRICE_KSH": 500}
WALLET = {"WELCOME_COINS": 0, "KSH_PER_COIN": 1.0, "MAX_GIFT_QUANTITY": 99}


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise mpesa.requests.HTTPError(str(self.status_code))


def callback(checkout_id, code=0, amount=500, receipt="SGH7XYZ123"):
    body = {"MerchantRequestID": "m1", "CheckoutRequestID": checkout_id, "ResultCode": code, "ResultDesc": "x"}
    if code == 0:
        body["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": amount},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "PhoneNumber", "Value": 254712345678},
            ]
        }
    return {"Body": {"stkCallback": body}}


@override_settings(MPESA=MPESA, PAYMENTS=PAYMENTS, WALLET=WALLET)
class MpesaPaymentTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.make_user("payer")
        self.auth(self.user)
        self.get = mock.patch.object(
            mpesa.requests, "get", return_value=FakeResponse({"access_token": "tok", "expires_in": "3599"})
        ).start()
        self.post = mock.patch.object(
            mpesa.requests,
            "post",
            return_value=FakeResponse({"MerchantRequestID": "m1", "CheckoutRequestID": "ws_CO_1", "ResponseCode": "0"}),
        ).start()

    def cb_url(self, token="cb-token-123"):
        return reverse("payments-mpesa-callback", args=[token])

    # Phone number -----------------------------------------------------------
    def test_phone_saved_normalised_and_private(self):
        res = self.client.patch(reverse("users-me"), {"phone_number": "0712 345 678"}, format="json")
        self.assertEqual(self.assertOk(res)["phone_number"], "254712345678")
        other = self.make_user("other")
        self.auth(other)
        profile = self.assertOk(self.client.get(reverse("users-detail", args=[self.user.pk])))
        self.assertNotIn("phone_number", profile)
        dup = self.client.patch(reverse("users-me"), {"phone_number": "+254712345678"}, format="json")
        self.assertError(dup, 400, "validation_error")

    def test_bad_phone_rejected(self):
        res = self.client.patch(reverse("users-me"), {"phone_number": "12345"}, format="json")
        self.assertError(res, 400, "validation_error")

    # Verification -------------------------------------------------------------
    def test_verification_paid_by_callback(self):
        info = self.assertOk(self.client.get(reverse("payments-verification")))
        self.assertEqual((info["price"], info["available"], info["is_verified"]), (500, True, False))
        p = self.assertOk(self.client.post(reverse("payments-verification"), {"phone": "0712345678"}), 201)
        self.assertEqual((p["status"], p["amount"], p["phone"]), ("pending", 500, "0712 345 678"))
        body = self.post.call_args.kwargs["json"]
        self.assertEqual(
            (body["Amount"], body["PartyA"], body["TransactionType"]), (500, "254712345678", "CustomerPayBillOnline")
        )
        self.assertTrue(body["CallBackURL"].endswith("/api/payments/mpesa/callback/cb-token-123/"))

        self.client.force_authenticate(None)
        self.assertOk(self.client.post(self.cb_url(), callback("ws_CO_1"), format="json"))
        self.assertOk(self.client.post(self.cb_url(), callback("ws_CO_1"), format="json"))  # repeated: no effect
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.auth(self.user)
        done = self.assertOk(self.client.get(reverse("payments-detail", args=[p["id"]])))
        self.assertEqual((done["status"], done["receipt"]), ("paid", "SGH7XYZ123"))
        again = self.client.post(reverse("payments-verification"), {"phone": "0712345678"})
        self.assertError(again, 409, "already_verified")

    def test_callback_with_wrong_token_or_short_amount(self):
        p = self.assertOk(self.client.post(reverse("payments-verification"), {"phone": "0712345678"}), 201)
        self.assertEqual(self.client.post(self.cb_url("wrong"), callback("ws_CO_1"), format="json").status_code, 404)
        self.client.post(self.cb_url(), callback("ws_CO_1", amount=1), format="json")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)
        self.assertEqual(MpesaPayment.objects.get(pk=p["id"]).status, "failed")

    def test_cancelled_by_customer(self):
        p = self.assertOk(self.client.post(reverse("payments-verification"), {"phone": "0712345678"}), 201)
        self.client.post(self.cb_url(), callback("ws_CO_1", code=1032), format="json")
        res = self.assertOk(self.client.get(reverse("payments-detail", args=[p["id"]])))
        self.assertEqual(res["status"], "cancelled")
        self.assertIn("cancelled", res["message"])

    def test_missing_callback_settled_by_status_query(self):
        p = self.assertOk(self.client.post(reverse("payments-verification"), {"phone": "0712345678"}), 201)
        MpesaPayment.objects.filter(pk=p["id"]).update(created_at=mpesa_now_minus(30))
        self.post.return_value = FakeResponse({"ResultCode": "0", "ResultDesc": "ok"})
        res = self.assertOk(self.client.get(reverse("payments-detail", args=[p["id"]])))
        self.assertEqual(res["status"], "paid")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        # The late callback still records the receipt, without paying twice.
        self.client.force_authenticate(None)
        self.client.post(self.cb_url(), callback("ws_CO_1"), format="json")
        self.assertEqual(MpesaPayment.objects.get(pk=p["id"]).receipt, "SGH7XYZ123")

    def test_stk_refused_is_reported_safely(self):
        self.post.return_value = FakeResponse(
            {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}, status=400
        )
        res = self.client.post(reverse("payments-verification"), {"phone": "0712345678"})
        self.assertError(res, 400, "mpesa_failed")
        self.assertNotIn("400.002", str(res.json()))

    @override_settings(MPESA={**MPESA, "PASSKEY": ""})
    def test_not_configured_is_coming_soon(self):
        self.assertFalse(self.assertOk(self.client.get(reverse("payments-verification")))["available"])
        res = self.client.post(reverse("payments-verification"), {"phone": "0712345678"})
        self.assertError(res, 503, "coming_soon")

    # Coins --------------------------------------------------------------------
    def test_buy_coins_with_mpesa(self):
        opts = self.assertOk(self.client.get(reverse("wallet-recharge")))
        self.assertTrue(opts["available"])
        self.assertEqual(
            {m["id"]: m["available"] for m in opts["methods"]}, {"mpesa": True, "airtel": False, "card": False}
        )
        p = self.assertOk(
            self.client.post(reverse("wallet-recharge"), {"method": "mpesa", "coins": 1000, "phone": "0712345678"}),
            201,
        )
        self.assertEqual((p["amount"], p["coins"], p["purpose"]), (1000, 1000, "coins"))
        self.client.force_authenticate(None)
        self.client.post(self.cb_url(), callback("ws_CO_1", amount=1000), format="json")
        self.client.post(self.cb_url(), callback("ws_CO_1", amount=1000), format="json")
        self.auth(self.user)
        self.assertEqual(self.assertOk(self.client.get(reverse("wallet")))["balance"], 1000)
        self.assertEqual(WalletTransaction.objects.filter(user=self.user, kind="recharge").count(), 1)

    def test_other_methods_still_coming_soon(self):
        res = self.client.post(reverse("wallet-recharge"), {"method": "card", "coins": 100})
        self.assertError(res, 503, "coming_soon")

    def test_saved_phone_used_when_none_sent(self):
        self.user.phone_number = "254798765432"
        self.user.save(update_fields=["phone_number"])
        self.assertOk(self.client.post(reverse("wallet-recharge"), {"method": "mpesa", "coins": 100}), 201)
        self.assertEqual(self.post.call_args.kwargs["json"]["PartyA"], "254798765432")

    @override_settings(PAYMENTS={**PAYMENTS, "DIRECT_ENABLED": False})
    def test_play_store_mode_offers_only_google_play(self):
        opts = self.assertOk(self.client.get(reverse("wallet-recharge")))
        self.assertEqual([m["id"] for m in opts["methods"]], ["google_play"])
        self.assertFalse(opts["available"])
        res = self.client.post(reverse("payments-verification"), {"phone": "0712345678"})
        self.assertError(res, 503, "coming_soon")


def mpesa_now_minus(seconds):
    from datetime import timedelta

    from django.utils import timezone

    return timezone.now() - timedelta(seconds=seconds)
