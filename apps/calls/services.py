"""
Voice and video calls between people who follow each other.

Media runs through LiveKit (one private room per call). Signalling goes two
ways at once so a ring is never missed:

* a high-priority FCM data message wakes the callee's phone (the app shows a
  full-screen incoming-call screen with a ringtone, even when closed);
* the call record is the source of truth, so either app can poll
  ``GET /api/calls/{id}/`` while a call is ringing or in progress.

A call that rings for ``RING_TIMEOUT`` without an answer becomes missed and
the callee gets a "Missed call" notification.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.blocks.services import ensure_not_blocked, unavailable
from apps.core.exceptions import ServiceError
from apps.follows.models import Follow
from apps.live import livekit
from apps.notifications import sync
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify
from apps.users.serializers import avatar_payload

from .models import Call

User = get_user_model()

RING_TIMEOUT = timedelta(seconds=45)
# A connected call is closed if neither side reported back for this long (app killed, phone off).
MAX_CALL_LENGTH = timedelta(hours=4)
TOKEN_TTL_SECONDS = 4 * 60 * 60


class CallsUnavailable(ServiceError):
    status_code = 503
    default_code = "calls_unavailable"
    default_detail = "Calling is not available right now."


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def mutual_follow(a, b) -> bool:
    return Follow.objects.filter(Q(follower=a, following=b) | Q(follower=b, following=a)).values("pk").count() == 2


def check_can_call(caller, callee):
    if caller.pk == callee.pk:
        raise ServiceError("You can't call yourself.", code="self_call")
    if not callee.is_active:
        raise unavailable()
    ensure_not_blocked(caller, callee, "Unblock this account to call it.")
    if not mutual_follow(caller, callee):
        raise ServiceError("You can call people who follow you back.", code="not_mutual", status_code=403)
    if not callee.allow_calls:
        raise ServiceError("This person isn't taking calls.", code="calls_off", status_code=403)


def expire_stale():
    """Ringing too long -> missed. Very old connected calls -> ended."""
    now = timezone.now()
    for call in Call.objects.filter(status=Call.Status.RINGING, created_at__lt=now - RING_TIMEOUT).select_related(
        "caller", "callee"
    ):
        _finish(call, Call.Status.MISSED, by=None)
    Call.objects.filter(status=Call.Status.ACCEPTED, answered_at__lt=now - MAX_CALL_LENGTH).update(
        status=Call.Status.ENDED, ended_at=now
    )


def active_call_for(user):
    return Call.objects.filter(Q(caller=user) | Q(callee=user), status__in=Call.ACTIVE).first()


def get_call(user, call_id) -> Call:
    expire_stale()
    call = Call.objects.select_related("caller__profile_image", "callee__profile_image").filter(pk=call_id).first()
    if call is None or user.pk not in (call.caller_id, call.callee_id):
        raise ServiceError("Call not found.", code="not_found", status_code=404)
    return call


def connection(call, user) -> dict:
    token = livekit.access_token(
        room=call.room_name,
        identity=str(user.pk),
        name=user.username,
        can_publish=True,
        metadata={"username": user.username, "call": str(call.pk)},
        ttl_seconds=TOKEN_TTL_SECONDS,
    )
    return {"url": livekit.server_url(), "token": token, "room": call.room_name, "identity": str(user.pk)}


# ---------------------------------------------------------------------------
# Signalling
# ---------------------------------------------------------------------------
def _signal(call, event, recipients):
    """Wake the apps of ``recipients`` with the call's new state."""
    caller = call.caller
    avatar = avatar_payload(caller.profile_image) or {}
    sync.emit(
        recipients,
        "call",
        collapse_key=f"call:{call.pk}",
        ttl_seconds=int(RING_TIMEOUT.total_seconds()),
        call_id=str(call.pk),
        event=event,
        status=call.status,
        kind=call.kind,
        caller_id=caller.pk,
        caller_username=caller.username,
        caller_name=(caller.full_name or caller.username),
        caller_avatar=avatar.get("thumbnail") or "",
        created_at=call.created_at.isoformat(),
    )


def _finish(call, status, *, by):
    """Move a call to a final state, tell both sides, close the room."""
    if call.status not in Call.ACTIVE:
        return call
    was_ringing = call.status == Call.Status.RINGING
    call.status = status
    call.ended_at = timezone.now()
    call.ended_by = by
    call.save(update_fields=["status", "ended_at", "ended_by"])
    if was_ringing and status in (Call.Status.MISSED, Call.Status.CANCELLED):
        notify(
            recipient=call.callee,
            sender=call.caller,
            notification_type=NotificationType.MISSED_CALL,
            target_type=TargetType.CALL,
            reference_id=call.pk,
            preview=f"Missed {call.kind} call",
        )
    _signal(call, status, [call.caller_id, call.callee_id])
    transaction.on_commit(lambda: livekit.end_room(call.room_name))
    return call


@transaction.atomic
def start(caller, callee_id, kind) -> Call:
    if not livekit.is_configured():
        raise CallsUnavailable()
    callee = User.objects.filter(pk=callee_id).first()
    if callee is None:
        raise unavailable()
    check_can_call(caller, callee)
    expire_stale()
    if active_call_for(caller):
        raise ServiceError("You're already on a call.", code="already_in_call", status_code=409)
    busy = active_call_for(callee) is not None
    call = Call.objects.create(
        caller=caller,
        callee=callee,
        kind=kind,
        status=Call.Status.BUSY if busy else Call.Status.RINGING,
        ended_at=timezone.now() if busy else None,
    )
    if busy:
        notify(
            recipient=callee,
            sender=caller,
            notification_type=NotificationType.MISSED_CALL,
            target_type=TargetType.CALL,
            reference_id=call.pk,
            preview=f"Missed {kind} call",
        )
    else:
        _signal(call, "incoming", [callee.pk])
    return call


@transaction.atomic
def accept(user, call_id) -> Call:
    call = get_call(user, call_id)
    if call.callee_id != user.pk:
        raise ServiceError("Only the person being called can answer.", code="permission_denied", status_code=403)
    if call.status == Call.Status.ACCEPTED:
        return call
    if call.status != Call.Status.RINGING:
        raise ServiceError("This call has ended.", code="call_ended", status_code=410)
    call.status = Call.Status.ACCEPTED
    call.answered_at = timezone.now()
    call.save(update_fields=["status", "answered_at"])
    _signal(call, "accepted", [call.caller_id, call.callee_id])
    return call


@transaction.atomic
def decline(user, call_id) -> Call:
    call = get_call(user, call_id)
    if call.callee_id != user.pk:
        raise ServiceError("Only the person being called can decline.", code="permission_denied", status_code=403)
    return _finish(call, Call.Status.DECLINED, by=user)


@transaction.atomic
def cancel(user, call_id) -> Call:
    """The caller hangs up before an answer."""
    call = get_call(user, call_id)
    if call.caller_id != user.pk:
        raise ServiceError("Only the caller can cancel.", code="permission_denied", status_code=403)
    if call.status == Call.Status.ACCEPTED:
        return _finish(call, Call.Status.ENDED, by=user)
    return _finish(call, Call.Status.CANCELLED, by=user)


@transaction.atomic
def end(user, call_id, *, failed=False) -> Call:
    """Either side hangs up. A ringing call ends as cancelled (caller) or declined (callee)."""
    call = get_call(user, call_id)
    if call.status == Call.Status.RINGING:
        if user.pk == call.caller_id:
            return _finish(call, Call.Status.CANCELLED, by=user)
        return _finish(call, Call.Status.DECLINED, by=user)
    return _finish(call, Call.Status.FAILED if failed else Call.Status.ENDED, by=user)


def history(user):
    expire_stale()
    return (
        Call.objects.filter(Q(caller=user) | Q(callee=user))
        .select_related("caller__profile_image", "callee__profile_image")
        .order_by("-created_at")
    )
