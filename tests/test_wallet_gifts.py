from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.follows.models import Follow
from apps.live.models import LiveInvite, LiveStream
from apps.wallet.models import GiftSend, Wallet, WalletTransaction

from .base import FlashXTestCase

LK = {"URL": "wss://flashx.livekit.cloud", "API_KEY": "APIkey", "API_SECRET": "s" * 40}
WALLET = {"WELCOME_COINS": 500, "KSH_PER_COIN": 1.0, "MAX_GIFT_QUANTITY": 99}


@override_settings(LIVEKIT=LK, WALLET=WALLET)
class WalletGiftTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.host = self.make_user("host")
        self.fan = self.make_user("fan")
        Follow.objects.create(follower=self.fan, following=self.host)
        self.server = mock.patch("requests.post").start()
        self.server.return_value.status_code = 200
        self.auth(self.host)
        self.pk = self.assertOk(self.client.post(reverse("live-list"), {"title": "Gifts"}, format="json"), 201)[
            "stream"
        ]["id"]
        self.auth(self.fan)
        self.assertOk(self.client.post(reverse("live-join", args=[self.pk])))

    def gift(self, slug="sweets", quantity=1, **extra):
        return self.client.post(
            reverse("live-gifts", args=[self.pk]), {"gift": slug, "quantity": quantity, **extra}, format="json"
        )

    def test_welcome_bonus_once_and_catalogue(self):
        w = self.assertOk(self.client.get(reverse("wallet")))
        self.assertEqual((w["balance"], w["ksh_value"], w["payments_available"]), (500, 500.0, False))
        self.assertEqual([t["kind"] for t in w["recent"]], ["welcome_bonus"])
        self.assertEqual(self.assertOk(self.client.get(reverse("wallet")))["balance"], 500)
        self.assertEqual(WalletTransaction.objects.filter(user=self.fan).count(), 1)
        cat = self.assertOk(self.client.get(reverse("wallet-gifts")))["results"]
        self.assertEqual(cat[0]["slug"], "sweets")
        self.assertIn({"slug": "crown", "coins": 100000}, [{"slug": g["slug"], "coins": g["coins"]} for g in cat])

    def test_send_gift_moves_coins_and_shows_in_chat(self):
        sent = self.assertOk(self.gift("rose", 3, client_id="abc"), 201)
        self.assertEqual((sent["coins"], sent["balance"], sent["recipient_id"]), (60, 440, self.host.pk))
        # A retry with the same client_id doesn't charge twice.
        again = self.assertOk(self.gift("rose", 3, client_id="abc"), 201)
        self.assertEqual((again["id"], again["balance"]), (sent["id"], 440))
        self.assertEqual(GiftSend.objects.count(), 1)
        # The host's welcome bonus is added the first time they open their wallet.
        self.assertEqual(Wallet.objects.get(user=self.host).balance, 60)

        state = self.assertOk(self.client.get(reverse("live-comments", args=[self.pk])))
        line = [c for c in state["comments"] if c["kind"] == "gift"][0]
        self.assertEqual(
            (line["gift"]["slug"], line["gift"]["quantity"], line["gift"]["recipient"]["id"]),
            ("rose", 3, self.host.pk),
        )
        self.assertEqual(state["gifts_coins"], 60)

        self.auth(self.host)
        self.assertEqual(self.assertOk(self.client.get(reverse("wallet")))["balance"], 560)
        received = self.assertOk(self.client.get(reverse("wallet-gift-history")))["results"]
        self.assertEqual((received[0]["user"]["username"], received[0]["coins"]), ("fan", 60))
        txs = self.assertOk(self.client.get(reverse("wallet-transactions"), {"type": "gifts_received"}))["results"]
        self.assertEqual((txs[0]["title"], txs[0]["subtitle"], txs[0]["amount"]), ("Gift received", "From fan", 60))
        stats = self.assertOk(self.client.get(reverse("live-stats", args=[self.pk])))
        self.assertEqual((stats["gifts_coins"], stats["top_gifters"][0]["username"]), (60, "fan"))

    def test_insufficient_coins(self):
        err = self.assertError(self.gift("crown"), 402, "insufficient_coins")
        self.assertEqual(err["details"], {"balance": 500, "needed": 100000})
        self.assertEqual(Wallet.objects.get(user=self.fan).balance, 500)
        self.assertFalse(GiftSend.objects.exists())

    def test_recipient_rules(self):
        stranger = self.make_user()
        self.assertError(self.gift(recipient_id=stranger.pk), 400, "invalid_recipient")
        guest = self.make_user("guest")
        LiveInvite.objects.create(stream_id=self.pk, user=guest, status=LiveInvite.Status.ACCEPTED)
        self.assertOk(self.gift(recipient_id=guest.pk), 201)
        self.auth(self.host)
        self.assertError(self.gift(), 400, "invalid_recipient")  # the host can't gift themselves
        self.assertError(self.gift("nonexistent"), 400, "gift_unavailable")
        self.auth(self.fan)
        self.assertError(self.gift(quantity=1000), 400, "validation_error")
        LiveStream.objects.filter(pk=self.pk).update(status=LiveStream.Status.ENDED)
        self.assertError(self.gift(), 410, "live_ended")

    def test_clients_cannot_post_gift_lines(self):
        res = self.client.post(reverse("live-comments", args=[self.pk]), {"text": "x", "kind": "gift"}, format="json")
        self.assertError(res, 400, "validation_error")

    def test_payments_and_transfers_are_coming_soon(self):
        opts = self.assertOk(self.client.get(reverse("wallet-recharge")))
        self.assertFalse(opts["available"])
        self.assertEqual([m["id"] for m in opts["methods"]], ["google_play"])
        self.assertError(
            self.client.post(reverse("wallet-recharge"), {"method": "google_play", "coins": 100}, format="json"),
            503,
            "coming_soon",
        )
        self.assertError(
            self.client.post(reverse("wallet-transfer"), {"recipient_id": self.host.pk, "coins": 10}, format="json"),
            503,
            "coming_soon",
        )
