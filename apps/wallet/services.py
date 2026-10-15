"""
Coins and live gifts.

Every balance change is one or two WalletTransaction rows written in the
same database transaction as the balance update, with the wallets locked
(always in user id order, so two people gifting each other can't deadlock).

Buying coins and sending coins between people are not open yet: there is no
payment provider, and coins sold inside the Android app would have to go
through Google Play Billing. Until then people get a one-time welcome bonus
(awarded coins, not sold) and the coins of gifts they receive.
"""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from apps.core.exceptions import ServiceError

from .models import GiftItem, GiftSend, Wallet, WalletTransaction

RECHARGE_PACKAGES = (100, 500, 1000, 2000, 5000, 10000)
# Coins are digital goods used inside the app, so the Android app may only
# sell them through Google Play's billing system (Play payments policy).
RECHARGE_METHODS = (("google_play", "Google Play", "Pay with your Google account"),)


def _cfg():
    return getattr(settings, "WALLET", {})


def welcome_coins() -> int:
    return int(_cfg().get("WELCOME_COINS", 500))


def ksh_per_coin() -> float:
    return float(_cfg().get("KSH_PER_COIN", 1))


def max_quantity() -> int:
    return int(_cfg().get("MAX_GIFT_QUANTITY", 99))


def coming_soon(feature="Payments") -> ServiceError:
    return ServiceError(f"{feature} are coming soon.", code="coming_soon", status_code=503)


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------
def _lock(user) -> Wallet:
    """The user's wallet, locked for this transaction (created on first use)."""
    Wallet.objects.get_or_create(user=user)
    return Wallet.objects.select_for_update().get(user=user)


def _post(wallet, kind, amount, **extra) -> WalletTransaction:
    wallet.balance += amount
    if wallet.balance < 0:
        raise ServiceError("Not enough coins.", code="insufficient_coins")
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        user_id=wallet.user_id, kind=kind, amount=amount, balance_after=wallet.balance, **extra
    )


@transaction.atomic
def get_wallet(user) -> Wallet:
    """The wallet, with the welcome bonus added the first time it is opened."""
    wallet = _lock(user)
    bonus = welcome_coins()
    if not wallet.welcome_bonus_given:
        wallet.welcome_bonus_given = True
        wallet.save(update_fields=["welcome_bonus_given", "updated_at"])
        if bonus > 0:
            _post(wallet, WalletTransaction.Kind.WELCOME_BONUS, bonus, note="Welcome to FlashX")
    return wallet


def summary(user) -> dict:
    wallet = get_wallet(user)
    totals = dict(
        WalletTransaction.objects.filter(user=user)
        .values_list("kind")
        .annotate(total=Sum("amount"))
        .values_list("kind", "total")
    )
    recent = (
        WalletTransaction.objects.filter(user=user)
        .select_related("counterparty", "gift_send__gift")
        .order_by("-created_at", "-id")[:5]
    )
    return {
        "balance": wallet.balance,
        "ksh_value": round(wallet.balance * ksh_per_coin(), 2),
        "ksh_per_coin": ksh_per_coin(),
        "received_coins": int(totals.get(WalletTransaction.Kind.GIFT_RECEIVED) or 0),
        "sent_coins": -int(totals.get(WalletTransaction.Kind.GIFT_SENT) or 0),
        "recent": [transaction_payload(t) for t in recent],
        "payments_available": False,
    }


def transactions(user, kind="all"):
    qs = WalletTransaction.objects.filter(user=user).select_related("counterparty", "gift_send__gift")
    kinds = {
        "gifts_sent": [WalletTransaction.Kind.GIFT_SENT],
        "gifts_received": [WalletTransaction.Kind.GIFT_RECEIVED],
        "gifts": [WalletTransaction.Kind.GIFT_SENT, WalletTransaction.Kind.GIFT_RECEIVED],
        "recharge": [WalletTransaction.Kind.RECHARGE],
        "bonus": [WalletTransaction.Kind.WELCOME_BONUS, WalletTransaction.Kind.ADJUSTMENT],
        "transfers": [WalletTransaction.Kind.TRANSFER_IN, WalletTransaction.Kind.TRANSFER_OUT],
    }.get(kind)
    if kinds:
        qs = qs.filter(kind__in=kinds)
    return qs.order_by("-created_at", "-id")


def gift_history(user, direction="received"):
    qs = GiftSend.objects.select_related("gift", "sender__profile_image", "recipient__profile_image")
    qs = qs.filter(sender=user) if direction == "sent" else qs.filter(recipient=user)
    return qs.order_by("-created_at", "-id")


TITLES = {
    WalletTransaction.Kind.WELCOME_BONUS: "Welcome bonus",
    WalletTransaction.Kind.GIFT_SENT: "Gift sent",
    WalletTransaction.Kind.GIFT_RECEIVED: "Gift received",
    WalletTransaction.Kind.RECHARGE: "Recharge",
    WalletTransaction.Kind.TRANSFER_IN: "Coins received",
    WalletTransaction.Kind.TRANSFER_OUT: "Coins sent",
    WalletTransaction.Kind.ADJUSTMENT: "Adjustment",
}


def transaction_payload(t: WalletTransaction) -> dict:
    other = t.counterparty
    subtitle = t.note
    if other is not None:
        subtitle = f"{'To' if t.amount < 0 else 'From'} {other.username}"
    send = t.gift_send
    return {
        "id": t.pk,
        "kind": t.kind,
        "title": TITLES.get(t.kind, t.kind),
        "subtitle": subtitle,
        "amount": t.amount,
        "balance_after": t.balance_after,
        "gift": gift_ref(send.gift, send.quantity) if send is not None else None,
        "counterparty": {"id": other.pk, "username": other.username} if other is not None else None,
        "created_at": t.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Gifts
# ---------------------------------------------------------------------------
def catalog():
    return GiftItem.objects.filter(is_active=True).order_by("sort_order", "coins", "id")


def gift_ref(gift: GiftItem, quantity=1) -> dict:
    return {
        "slug": gift.slug,
        "name": gift.name,
        "coins": gift.coins,
        "size": gift.size,
        "category": gift.category,
        "quantity": quantity,
    }


def gift_payload(gift: GiftItem) -> dict:
    return {k: v for k, v in gift_ref(gift).items() if k != "quantity"} | {"id": gift.pk}


def send_live_gift(stream, sender, *, gift_slug, quantity=1, recipient_id=None, client_id=""):
    """
    Send a gift to the host (or a guest on screen). Charges the sender,
    credits the recipient, and posts a gift line in the live chat that every
    viewer's app turns into the gift animation.
    """
    from apps.live import services as live
    from apps.live.models import LiveComment, LiveInvite, LiveStream

    client_id = (client_id or "").strip()[:64]
    if client_id:
        existing = GiftSend.objects.filter(sender=sender, client_id=client_id).select_related("gift").first()
        if existing is not None:
            return existing, get_wallet(sender).balance
    if stream.status != LiveStream.Status.LIVE:
        raise ServiceError("This live video has ended.", code="live_ended", status_code=410)
    if live.is_banned(stream, sender):
        raise ServiceError("You were removed from this live video.", code="live_banned", status_code=403)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= max_quantity():
        raise ServiceError(f"Send between 1 and {max_quantity()} at a time.", code="validation_error")
    gift = GiftItem.objects.filter(slug=gift_slug, is_active=True).first()
    if gift is None:
        raise ServiceError("That gift isn't available.", code="gift_unavailable")

    recipient_id = int(recipient_id or stream.host_id)
    on_screen = (
        recipient_id == stream.host_id
        or LiveInvite.objects.filter(stream=stream, user_id=recipient_id, status=LiveInvite.Status.ACCEPTED).exists()
    )
    if not on_screen:
        raise ServiceError("You can only send gifts to the host or a guest on screen.", code="invalid_recipient")
    if recipient_id == sender.pk:
        raise ServiceError("You can't send a gift to yourself.", code="invalid_recipient")
    from django.contrib.auth import get_user_model

    recipient = get_user_model().objects.filter(pk=recipient_id, is_active=True).first()
    if recipient is None:
        raise ServiceError("That account isn't available.", code="invalid_recipient")
    from apps.blocks.selectors import is_blocked_between

    if is_blocked_between(sender, recipient):
        raise ServiceError("You can't send gifts to this account.", code="invalid_recipient")

    total = gift.coins * quantity
    get_wallet(sender)  # make sure the welcome bonus is in before checking the balance
    try:
        with transaction.atomic():
            first, second = sorted([sender, recipient], key=lambda u: u.pk)
            wallets = {first.pk: _lock(first), second.pk: _lock(second)}
            payer = wallets[sender.pk]
            if payer.balance < total:
                raise ServiceError(
                    "You don't have enough coins for this gift.",
                    code="insufficient_coins",
                    status_code=402,
                    details={"balance": payer.balance, "needed": total},
                )
            send = GiftSend.objects.create(
                sender=sender,
                recipient=recipient,
                gift=gift,
                quantity=quantity,
                coins=total,
                stream=stream,
                client_id=client_id,
            )
            _post(payer, WalletTransaction.Kind.GIFT_SENT, -total, gift_send=send, counterparty=recipient)
            _post(
                wallets[recipient.pk],
                WalletTransaction.Kind.GIFT_RECEIVED,
                total,
                gift_send=send,
                counterparty=sender,
            )
            LiveComment.objects.create(
                stream=stream,
                user=sender,
                kind=LiveComment.Kind.GIFT,
                text=f"sent {gift.name} x{quantity}"[:300],
                gift_send=send,
            )
            LiveStream.objects.filter(pk=stream.pk).update(gifts_coins=F("gifts_coins") + total)
            balance = payer.balance
    except IntegrityError:
        # The same client_id raced in twice: the first one won.
        existing = GiftSend.objects.filter(sender=sender, client_id=client_id).select_related("gift").first()
        if existing is None:
            raise
        return existing, get_wallet(sender).balance
    return send, balance


def recharge_options() -> dict:
    return {
        "available": False,
        "message": "Buying coins is coming soon.",
        "ksh_per_coin": ksh_per_coin(),
        "methods": [{"id": i, "name": n, "detail": d, "available": False} for i, n, d in RECHARGE_METHODS],
        "packages": [{"coins": c, "ksh": round(c * ksh_per_coin(), 2)} for c in RECHARGE_PACKAGES],
    }
