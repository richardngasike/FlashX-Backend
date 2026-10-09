"""
Push notifications through Firebase Cloud Messaging (HTTP v1 API).

Configure with FCM_SERVICE_ACCOUNT_JSON: the Firebase service-account key,
either as raw JSON or base64-encoded JSON. When it is empty, push is off and
every function here is a no-op.

Android shows notifications on the channels the app creates
(``flashx_messages`` and ``flashx_activity``), both playing the bundled
``flashx_notification`` sound. iOS plays the same ``flashx_notification.wav``.
"""

import base64
import json
import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TIMEOUT_SECONDS = 4

CHANNEL_MESSAGES = "flashx_messages"
CHANNEL_ACTIVITY = "flashx_activity"
SOUND_ANDROID = "flashx_notification"
SOUND_IOS = "flashx_notification.wav"

# Errors that mean the token will never work again.
_DEAD_TOKEN_ERRORS = {"UNREGISTERED", "NOT_FOUND"}

_lock = threading.Lock()
_credentials = None
_project_id = None


class PushConfigurationError(Exception):
    pass


def _raw_key() -> str:
    return (settings.FCM.get("SERVICE_ACCOUNT_JSON") or "").strip()


def is_enabled() -> bool:
    return bool(_raw_key())


def _service_account_info() -> dict:
    raw = _raw_key()
    if not raw.startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PushConfigurationError("FCM_SERVICE_ACCOUNT_JSON is neither JSON nor base64 JSON.") from exc
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise PushConfigurationError("FCM_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    if "project_id" not in info or "private_key" not in info:
        raise PushConfigurationError("FCM_SERVICE_ACCOUNT_JSON is not a service-account key.")
    return info


def _reset_credentials():
    global _credentials, _project_id
    with _lock:
        _credentials = None
        _project_id = None


def _access_token() -> tuple[str, str]:
    """OAuth token for FCM, cached per process and refreshed when it expires."""
    global _credentials, _project_id
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with _lock:
        if _credentials is None:
            info = _service_account_info()
            _credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
            _project_id = info["project_id"]
        if not _credentials.valid:
            _credentials.refresh(Request())
        return _credentials.token, _project_id


def build_message(token, *, title, body, data, channel, tag="", badge=None, high_priority=False) -> dict:
    aps = {"sound": SOUND_IOS, "alert": {"title": title, "body": body}}
    if tag:
        aps["thread-id"] = tag
    if badge is not None:
        aps["badge"] = int(badge)
    android_notification = {
        "channel_id": channel,
        "sound": SOUND_ANDROID,
        "default_vibrate_timings": True,
        "visibility": "PRIVATE",
    }
    if tag:
        android_notification["tag"] = tag
    if badge is not None:
        # Launchers that show numbers (Samsung, Xiaomi, ...) use this for the app icon badge.
        android_notification["notification_count"] = int(badge)
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in data.items() if v is not None},
            "android": {"priority": "HIGH" if high_priority else "NORMAL", "notification": android_notification},
            "apns": {
                "headers": {"apns-priority": "10" if high_priority else "5"},
                "payload": {"aps": aps},
            },
        }
    }


def _error_status(response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return ""
    for detail in error.get("details", []):
        code = detail.get("errorCode")
        if code:
            return code
    return error.get("status", "")


def send_to_user_detailed(user_id, *, title, body, data, channel, tag="", badge=None, high_priority=False) -> dict:
    """
    Send to every device of ``user_id`` and report what happened:
    ``{"enabled", "devices", "sent", "removed", "errors"}``. Never raises.
    """
    result = {"enabled": is_enabled(), "devices": 0, "sent": 0, "removed": 0, "errors": []}
    if not result["enabled"]:
        return result
    import requests

    from .models import DeviceToken

    tokens = list(DeviceToken.objects.filter(user_id=user_id).values_list("pk", "token"))
    result["devices"] = len(tokens)
    if not tokens:
        return result
    try:
        access_token, project = _access_token()
    except Exception as exc:  # noqa: BLE001 - misconfiguration must never break the request
        logger.exception("Push disabled: could not load FCM credentials")
        result["errors"].append(f"credentials: {exc}")
        return result

    dead = []
    url = SEND_URL.format(project=project)
    for pk, token in tokens:
        payload = build_message(
            token, title=title, body=body, data=data, channel=channel, tag=tag, badge=badge, high_priority=high_priority
        )
        try:
            response = requests.post(
                url, json=payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            logger.warning("FCM request failed: %s", exc)
            result["errors"].append(f"network: {exc}")
            continue
        if response.status_code == 200:
            result["sent"] += 1
            continue
        status = _error_status(response)
        if response.status_code == 404 or status in _DEAD_TOKEN_ERRORS:
            dead.append(pk)
            result["errors"].append(f"device no longer registered ({status or response.status_code})")
        elif response.status_code == 401:
            _reset_credentials()
            logger.warning("FCM rejected the access token; credentials will be reloaded")
            result["errors"].append("Firebase rejected the server credentials (401)")
        else:
            logger.warning("FCM send failed (%s %s)", response.status_code, status)
            result["errors"].append(f"Firebase error {response.status_code} {status}".strip())
    if dead:
        DeviceToken.objects.filter(pk__in=dead).delete()
        result["removed"] = len(dead)
    return result


def send_to_user(user_id, *, title, body, data, channel, tag="", badge=None, high_priority=False) -> int:
    """Send to every device of ``user_id``. Returns how many deliveries FCM accepted."""
    return send_to_user_detailed(
        user_id,
        title=title,
        body=body,
        data=data,
        channel=channel,
        tag=tag,
        badge=badge,
        high_priority=high_priority,
    )["sent"]


def unread_badge(user_id) -> int:
    """Number shown on the app icon: unread notifications, messages included."""
    from .models import Notification

    return Notification.objects.filter(recipient_id=user_id, is_read=False).count()


# ---------------------------------------------------------------------------
# Notification -> push
# ---------------------------------------------------------------------------
_MESSAGE_TYPES = {"message", "share", "story_reply"}


def _display_name(user) -> str:
    return (user.full_name or "").strip() or user.username


def _content_for(notification) -> tuple[str, str, str, str]:
    """(title, body, channel, tag) for a stored notification."""
    from .models import TargetType
    from .serializers import MESSAGES

    sender = notification.sender
    name = _display_name(sender) if sender else "FlashX"
    preview = (notification.preview or "").strip()
    kind = notification.notification_type

    if kind in _MESSAGE_TYPES and notification.target_type == TargetType.CONVERSATION:
        from apps.messaging.models import Conversation

        convo = Conversation.objects.filter(pk=notification.reference_id).only("is_group", "title").first()
        title = f"{name} in {convo.title}" if convo and convo.is_group and convo.title else name
        body = preview or MESSAGES.get(kind, "sent you a message.")
        return title, body[:240], CHANNEL_MESSAGES, f"conversation:{notification.reference_id}"

    template = MESSAGES.get(kind, "")
    body = template.format(target=notification.target_type, preview=preview[:120]) if template else preview
    tag = f"{notification.target_type}:{notification.reference_id}"
    return name, body[:240] or "New activity on FlashX", CHANNEL_ACTIVITY, tag


def dispatch_notification(notification_id) -> int:
    """Push one stored notification to its recipient's devices. Never raises."""
    from .models import Notification

    try:
        notification = Notification.objects.select_related("sender", "recipient").filter(pk=notification_id).first()
        if notification is None or not notification.recipient.is_active:
            return 0
        title, body, channel, tag = _content_for(notification)
        badge = unread_badge(notification.recipient_id)
        data = {
            "notification_id": notification.pk,
            "type": notification.notification_type,
            "target_type": notification.target_type,
            "reference_id": notification.reference_id,
            "sender_id": notification.sender_id,
            "sender_username": notification.sender.username if notification.sender else None,
            "badge": badge,
        }
        return send_to_user(
            notification.recipient_id,
            title=title,
            body=body,
            data=data,
            channel=channel,
            tag=tag,
            badge=badge,
            high_priority=channel == CHANNEL_MESSAGES,
        )
    except Exception:  # noqa: BLE001 - push is best effort
        logger.exception("Push dispatch failed for notification %s", notification_id)
        return 0
