"""
Safaricom Daraja client: Lipa na M-PESA Online (STK push) and its status query.

Settings (settings.MPESA, from environment variables):
    ENVIRONMENT       "sandbox" or "production"
    CONSUMER_KEY      Daraja app consumer key
    CONSUMER_SECRET   Daraja app consumer secret
    SHORTCODE         Business short code: the paybill, or the store number
                      (head office) of a till
    PASSKEY           Lipa na M-PESA Online passkey for SHORTCODE
    TILL_NUMBER       Set for a Buy Goods till; leave empty for a paybill
    CALLBACK_BASE_URL Public https address of this API (defaults to the
                      Vercel production domain)
    CALLBACK_TOKEN    Long random secret; part of the callback path

No credentials or callback addresses are ever sent to the app.
"""

import base64
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TIMEOUT = 15
_HOSTS = {"sandbox": "https://sandbox.safaricom.co.ke", "production": "https://api.safaricom.co.ke"}
_NAIROBI = ZoneInfo("Africa/Nairobi")


class MpesaError(Exception):
    """Daraja refused or could not be reached. ``public`` is safe to show."""

    def __init__(self, message, public="We couldn't reach M-PESA. Please try again."):
        super().__init__(message)
        self.public = public


def _cfg() -> dict:
    return getattr(settings, "MPESA", {})


def _base() -> str:
    return _HOSTS.get(str(_cfg().get("ENVIRONMENT", "sandbox")).lower(), _HOSTS["sandbox"])


def callback_url() -> str:
    cfg = _cfg()
    base = (cfg.get("CALLBACK_BASE_URL") or "").strip().rstrip("/")
    if not base:
        host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
        base = f"https://{host}" if host else ""
    token = (cfg.get("CALLBACK_TOKEN") or "").strip()
    if not base or not token:
        return ""
    return f"{base}/api/payments/mpesa/callback/{token}/"


def is_configured() -> bool:
    cfg = _cfg()
    required = ("CONSUMER_KEY", "CONSUMER_SECRET", "SHORTCODE", "PASSKEY")
    return all((cfg.get(k) or "").strip() for k in required) and bool(callback_url())


def _access_token() -> str:
    cfg = _cfg()
    key = "mpesa:token:" + str(cfg.get("CONSUMER_KEY", ""))[-6:]
    token = cache.get(key)
    if token:
        return token
    try:
        r = requests.get(
            f"{_base()}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(cfg["CONSUMER_KEY"], cfg["CONSUMER_SECRET"]),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise MpesaError(f"token request failed: {exc}") from exc
    token = data.get("access_token")
    if not token:
        raise MpesaError("token missing from Daraja response")
    # Tokens last an hour; refresh a little early.
    cache.set(key, token, max(60, int(data.get("expires_in", 3599)) - 120))
    return token


def _password(timestamp: str) -> str:
    cfg = _cfg()
    raw = f"{cfg['SHORTCODE']}{cfg['PASSKEY']}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def _timestamp() -> str:
    return datetime.now(_NAIROBI).strftime("%Y%m%d%H%M%S")


def _post(path: str, body: dict) -> dict:
    try:
        r = requests.post(
            f"{_base()}{path}",
            json=body,
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=TIMEOUT,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise MpesaError(f"{path} failed: {exc}") from exc
    if r.status_code >= 400 and "errorCode" in data:
        data["_http_status"] = r.status_code
    return data


def stk_push(*, phone: str, amount: int, reference: str, description: str) -> dict:
    """Send the PIN prompt to ``phone``. Returns MerchantRequestID and CheckoutRequestID."""
    cfg = _cfg()
    timestamp = _timestamp()
    till = (cfg.get("TILL_NUMBER") or "").strip()
    body = {
        "BusinessShortCode": cfg["SHORTCODE"],
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": "CustomerBuyGoodsOnline" if till else "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": till or cfg["SHORTCODE"],
        "PhoneNumber": phone,
        "CallBackURL": callback_url(),
        "AccountReference": reference[:12],
        "TransactionDesc": description[:13],
    }
    data = _post("/mpesa/stkpush/v1/processrequest", body)
    if str(data.get("ResponseCode")) != "0" or not data.get("CheckoutRequestID"):
        detail = data.get("errorMessage") or data.get("ResponseDescription") or "unknown"
        public = "We couldn't reach M-PESA. Please try again."
        if "phone" in str(detail).lower() or "partya" in str(detail).lower():
            public = "Check the phone number and try again."
        raise MpesaError(f"STK push refused: {data.get('errorCode', '')} {detail}", public=public)
    return data


# Daraja answers this while the customer has not finished yet.
PROCESSING_CODES = {"500.001.1001"}


def stk_query(checkout_request_id: str) -> dict | None:
    """Final result of an STK push, or None while it is still being processed."""
    cfg = _cfg()
    timestamp = _timestamp()
    data = _post(
        "/mpesa/stkpushquery/v1/query",
        {
            "BusinessShortCode": cfg["SHORTCODE"],
            "Password": _password(timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        },
    )
    if str(data.get("errorCode", "")) in PROCESSING_CODES:
        return None
    if "ResultCode" not in data:
        logger.warning("STK query gave no result: %s", data.get("errorMessage") or data)
        return None
    return data
