"""
M-PESA payments: the verified badge and coin purchases.

Flow: start_* creates a pending MpesaPayment and sends the STK push (the PIN
prompt on the customer's phone). Safaricom then calls the callback with the
result; if that call is late or lost, refresh() asks Daraja directly when the
app checks the payment. Either way finish() runs once per payment, in a
transaction, and gives the badge or the coins exactly once.
"""

import logging
import math
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ServiceError
from apps.core.phone import display_phone

from . import mpesa
from .models import MpesaPayment

logger = logging.getLogger(__name__)

Status = MpesaPayment.Status
Purpose = MpesaPayment.Purpose

# After this long without any answer the prompt has certainly expired.
PENDING_LIMIT = timedelta(minutes=4)
QUERY_EVERY = timedelta(seconds=8)
QUERY_AFTER = timedelta(seconds=10)


def _cfg() -> dict:
    return getattr(settings, "PAYMENTS", {})


def direct_payments_enabled() -> bool:
    """Off for the Play Store build: digital goods there go through Google Play billing."""
    return bool(_cfg().get("DIRECT_ENABLED", True))


def mpesa_available() -> bool:
    return direct_payments_enabled() and mpesa.is_configured()


def verification_price() -> int:
    return int(_cfg().get("VERIFICATION_PRICE_KSH", 500))


def _unavailable() -> ServiceError:
    return ServiceError("M-PESA payments are coming soon.", code="coming_soon", status_code=503)


# ---------------------------------------------------------------------------
# Starting a payment
# ---------------------------------------------------------------------------
def _start(user, *, purpose, amount, phone, coins=0) -> MpesaPayment:
    if not mpesa_available():
        raise _unavailable()
    payment = MpesaPayment.objects.create(user=user, purpose=purpose, amount=amount, coins=coins, phone=phone)
    reference = "FlashX"
    description = "Verified badge" if purpose == Purpose.VERIFICATION else "FlashX coins"
    try:
        data = mpesa.stk_push(phone=phone, amount=amount, reference=reference, description=description)
    except mpesa.MpesaError as exc:
        logger.warning("M-PESA STK push failed for payment %s: %s", payment.pk, exc)
        payment.status = Status.FAILED
        payment.result_desc = str(exc)[:255]
        payment.save(update_fields=["status", "result_desc", "updated_at"])
        raise ServiceError(exc.public, code="mpesa_failed") from None
    payment.merchant_request_id = str(data.get("MerchantRequestID", ""))[:64]
    payment.checkout_request_id = str(data["CheckoutRequestID"])[:64]
    payment.save(update_fields=["merchant_request_id", "checkout_request_id", "updated_at"])
    return payment


def start_verification(user, phone: str) -> MpesaPayment:
    if user.is_verified:
        raise ServiceError("Your account is already verified.", code="already_verified", status_code=409)
    return _start(user, purpose=Purpose.VERIFICATION, amount=verification_price(), phone=phone)


def coins_price(coins: int, ksh_per_coin: float) -> int:
    # M-PESA takes whole shillings; never charge less than the coins are worth.
    return max(1, math.ceil(coins * ksh_per_coin))


def start_coin_purchase(user, phone: str, coins: int, ksh_per_coin: float) -> MpesaPayment:
    return _start(user, purpose=Purpose.COINS, amount=coins_price(coins, ksh_per_coin), phone=phone, coins=coins)


# ---------------------------------------------------------------------------
# Finishing a payment
# ---------------------------------------------------------------------------
_FINAL_BY_CODE = {0: Status.PAID, 1032: Status.CANCELLED, 1037: Status.EXPIRED, 1019: Status.EXPIRED}


def _metadata(callback: dict) -> dict:
    items = (callback.get("CallbackMetadata") or {}).get("Item") or []
    return {i.get("Name"): i.get("Value") for i in items if isinstance(i, dict)}


def _fulfil(payment: MpesaPayment) -> None:
    if payment.fulfilled:
        return
    if payment.purpose == Purpose.VERIFICATION:
        get_user_model().objects.filter(pk=payment.user_id).update(is_verified=True)
    elif payment.purpose == Purpose.COINS and payment.coins:
        from apps.wallet.services import credit_purchase

        credit_purchase(payment.user, payment.coins, note=f"M-PESA {payment.receipt}".strip())
    payment.fulfilled = True


def finish(checkout_request_id: str, *, result_code: int, result_desc: str = "", meta: dict | None = None):
    """Apply a final result once. Safe to call again for the same payment."""
    meta = meta or {}
    with transaction.atomic():
        payment = MpesaPayment.objects.select_for_update().filter(checkout_request_id=checkout_request_id).first()
        if payment is None:
            logger.warning("M-PESA result for unknown request %s", checkout_request_id)
            return None
        if payment.status != Status.PENDING:
            # A status query may have settled it first; keep the receipt the callback brings.
            if payment.status == Status.PAID and not payment.receipt and meta.get("MpesaReceiptNumber"):
                payment.receipt = str(meta["MpesaReceiptNumber"])[:20]
                payment.save(update_fields=["receipt", "updated_at"])
            return payment
        status = _FINAL_BY_CODE.get(result_code, Status.FAILED)
        if status == Status.PAID:
            paid = meta.get("Amount")
            if paid is not None and int(float(paid)) < payment.amount:
                logger.error("M-PESA paid %s for payment %s of %s; not fulfilled", paid, payment.pk, payment.amount)
                status = Status.FAILED
        payment.status = status
        payment.result_code = result_code
        payment.result_desc = (result_desc or "")[:255]
        if meta.get("MpesaReceiptNumber"):
            payment.receipt = str(meta["MpesaReceiptNumber"])[:20]
        if status == Status.PAID:
            _fulfil(payment)
        payment.save()
    return payment


def handle_callback(body: dict) -> None:
    callback = ((body or {}).get("Body") or {}).get("stkCallback") or {}
    checkout_id = callback.get("CheckoutRequestID")
    if not checkout_id:
        logger.warning("M-PESA callback without CheckoutRequestID")
        return
    try:
        code = int(callback.get("ResultCode"))
    except (TypeError, ValueError):
        logger.warning("M-PESA callback with bad ResultCode for %s", checkout_id)
        return
    finish(checkout_id, result_code=code, result_desc=str(callback.get("ResultDesc", "")), meta=_metadata(callback))


def refresh(payment: MpesaPayment) -> MpesaPayment:
    """For a payment still pending: ask Daraja when the callback is late."""
    if payment.status != Status.PENDING or not payment.checkout_request_id:
        return payment
    now = timezone.now()
    age = now - payment.created_at
    if age < QUERY_AFTER or (payment.last_checked_at and now - payment.last_checked_at < QUERY_EVERY):
        return payment
    MpesaPayment.objects.filter(pk=payment.pk).update(last_checked_at=now)
    try:
        result = mpesa.stk_query(payment.checkout_request_id) if mpesa.is_configured() else None
    except mpesa.MpesaError as exc:
        logger.warning("M-PESA status query failed for payment %s: %s", payment.pk, exc)
        result = None
    if result is not None:
        try:
            code = int(result.get("ResultCode"))
        except (TypeError, ValueError):
            code = None
        if code is not None:
            # The query carries no receipt or amount; the callback fills those in when it comes.
            return finish(payment.checkout_request_id, result_code=code, result_desc=str(result.get("ResultDesc", "")))
    if age > PENDING_LIMIT:
        with transaction.atomic():
            locked = MpesaPayment.objects.select_for_update().get(pk=payment.pk)
            if locked.status == Status.PENDING:
                locked.status = Status.EXPIRED
                locked.result_desc = "No answer from M-PESA in time."
                locked.save(update_fields=["status", "result_desc", "updated_at"])
            return locked
    payment.refresh_from_db()
    return payment


def refresh_pending(user, purpose) -> None:
    """Settle this user's open payments (e.g. the app was closed while paying)."""
    for payment in MpesaPayment.objects.filter(user=user, purpose=purpose, status=Status.PENDING)[:3]:
        refresh(payment)


# ---------------------------------------------------------------------------
# What the app sees
# ---------------------------------------------------------------------------
def _message(p: MpesaPayment) -> str:
    if p.status == Status.PENDING:
        return f"Check your phone ({display_phone(p.phone)}) and enter your M-PESA PIN."
    if p.status == Status.PAID:
        if p.purpose == Purpose.VERIFICATION:
            return "Payment received. Your account is now verified."
        return f"Payment received. {p.coins:,} coins were added to your wallet."
    if p.status == Status.CANCELLED:
        return "You cancelled the M-PESA request."
    if p.status == Status.EXPIRED:
        return "The M-PESA request timed out. Please try again."
    if p.result_code == 1:
        return "Your M-PESA balance is not enough for this payment."
    if p.result_code == 2001:
        return "The M-PESA PIN was wrong. Please try again."
    return "The payment didn't go through. Please try again."


def payload(p: MpesaPayment) -> dict:
    return {
        "id": p.pk,
        "purpose": p.purpose,
        "status": p.status,
        "amount": p.amount,
        "currency": "KES",
        "coins": p.coins,
        "phone": display_phone(p.phone),
        "receipt": p.receipt,
        "message": _message(p),
        "created_at": p.created_at.isoformat(),
    }
