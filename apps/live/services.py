"""
Live video rules. Video itself flows through LiveKit; this module keeps the
stream record, presence, comments and guest invites.

Presence is heartbeat based: the app polls ``comments/`` every few seconds,
which refreshes ``last_seen_at`` for viewers and ``host_seen_at`` for the
host. A viewer counts as watching while seen in the last ``VIEWER_WINDOW``;
a stream whose host has been silent for ``HOST_TIMEOUT`` is ended
automatically (app killed, phone lost signal).
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.blocks.selectors import exclude_blocked, hidden_user_ids, is_blocked_between
from apps.core.exceptions import ServiceError

from . import livekit
from .models import LiveComment, LiveInvite, LiveStream, LiveViewer

logger = logging.getLogger(__name__)

VIEWER_WINDOW = timedelta(seconds=45)
HOST_TIMEOUT = timedelta(seconds=90)
MAX_GUESTS = 3
FOLLOWER_PUSH_LIMIT = 200


class LiveUnavailable(ServiceError):
    status_code = 503
    default_code = "live_unavailable"
    default_detail = "Live video is not available right now."


def require_configured():
    if not livekit.is_configured():
        raise LiveUnavailable()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def active_viewers(stream):
    cutoff = timezone.now() - VIEWER_WINDOW
    return stream.viewers.filter(left_at__isnull=True, last_seen_at__gte=cutoff).exclude(user_id=stream.host_id)


def with_viewer_count(qs):
    cutoff = timezone.now() - VIEWER_WINDOW
    return qs.annotate(
        viewer_count=Count(
            "viewers",
            filter=Q(viewers__left_at__isnull=True, viewers__last_seen_at__gte=cutoff)
            & ~Q(viewers__user_id=F("host_id")),
            distinct=True,
        )
    )


def expire_stale():
    """End streams whose host stopped sending heartbeats."""
    cutoff = timezone.now() - HOST_TIMEOUT
    stale = list(
        LiveStream.objects.filter(status=LiveStream.Status.LIVE)
        .filter(Q(host_seen_at__lt=cutoff) | Q(host_seen_at__isnull=True, started_at__lt=cutoff))
        .values_list("pk", "room_name")
    )
    if not stale:
        return 0
    ids = [pk for pk, _ in stale]
    now = timezone.now()
    LiveStream.objects.filter(pk__in=ids).update(status=LiveStream.Status.ENDED, ended_at=now)
    LiveInvite.objects.filter(stream_id__in=ids).exclude(status=LiveInvite.Status.DECLINED).update(
        status=LiveInvite.Status.ENDED
    )
    LiveViewer.objects.filter(stream_id__in=ids, left_at__isnull=True).update(left_at=now)
    return len(ids)


def live_now(viewer):
    """Streams on air, people the viewer follows first."""
    from django.db.models import Exists, OuterRef

    from apps.follows.models import Follow

    expire_stale()
    qs = (
        LiveStream.objects.filter(status=LiveStream.Status.LIVE, host__is_active=True)
        .exclude(host=viewer)
        .select_related("host__profile_image")
        .annotate(followed=Exists(Follow.objects.filter(follower=viewer, following_id=OuterRef("host_id"))))
    )
    qs = exclude_blocked(qs, viewer, field="host_id")
    return with_viewer_count(qs).order_by("-followed", "-started_at")


def get_stream(pk, viewer) -> LiveStream:
    stream = with_viewer_count(LiveStream.objects.select_related("host__profile_image")).filter(pk=pk).first()
    if stream is None or not stream.host.is_active or is_blocked_between(viewer, stream.host):
        raise ServiceError("Live video not found.", code="not_found", status_code=404)
    return stream


def role_of(stream, user) -> str:
    if stream.host_id == user.pk:
        return "host"
    if LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.ACCEPTED).exists():
        return "guest"
    return "viewer"


def guests(stream):
    return [
        i.user
        for i in LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.ACCEPTED).select_related(
            "user__profile_image"
        )
    ]


def pending_invite_for(stream, user):
    return LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.PENDING).first()


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def connection(stream, user, role) -> dict:
    token = livekit.access_token(
        room=stream.room_name,
        identity=str(user.pk),
        name=user.username,
        can_publish=role in ("host", "guest"),
        admin=role == "host",
        metadata={"username": user.username, "role": role},
    )
    return {"url": livekit.server_url(), "token": token, "room": stream.room_name, "identity": str(user.pk)}


# ---------------------------------------------------------------------------
# Host actions
# ---------------------------------------------------------------------------
def start(host, title="") -> LiveStream:
    require_configured()
    # One stream per host: starting again replaces a stream left open.
    for old in LiveStream.objects.filter(host=host, status=LiveStream.Status.LIVE):
        end(old, host)
    with transaction.atomic():
        stream = LiveStream.objects.create(host=host, title=(title or "").strip()[:120], host_seen_at=timezone.now())
        notification_ids = _notify_followers(stream)
    if notification_ids:
        transaction.on_commit(lambda: _push_many(notification_ids))
    return stream


def _notify_followers(stream) -> list:
    """Store a "went live" notification for recent followers. Returns their ids for push."""
    from apps.follows.models import Follow
    from apps.notifications.models import Notification, NotificationType, TargetType

    hidden = hidden_user_ids(stream.host)
    follower_ids = list(
        Follow.objects.filter(following=stream.host, follower__is_active=True)
        .exclude(follower_id__in=hidden)
        .order_by("-created_at")
        .values_list("follower_id", flat=True)[:FOLLOWER_PUSH_LIMIT]
    )
    if not follower_ids:
        return []
    created = Notification.objects.bulk_create(
        [
            Notification(
                recipient_id=uid,
                sender=stream.host,
                notification_type=NotificationType.LIVE,
                target_type=TargetType.LIVE,
                reference_id=str(stream.pk),
                preview=stream.title or "Tap to watch",
            )
            for uid in follower_ids
        ]
    )
    return [n.pk for n in created if n.pk]


def _push_many(notification_ids):
    from apps.notifications import push

    if push.is_enabled():
        push.dispatch_many(notification_ids)


def end(stream, user) -> LiveStream:
    if stream.host_id != user.pk:
        raise ServiceError("Only the host can end this live video.", code="permission_denied", status_code=403)
    if stream.status == LiveStream.Status.ENDED:
        return stream
    now = timezone.now()
    stream.status = LiveStream.Status.ENDED
    stream.ended_at = now
    stream.save(update_fields=["status", "ended_at"])
    stream.invites.exclude(status=LiveInvite.Status.DECLINED).update(status=LiveInvite.Status.ENDED)
    stream.viewers.filter(left_at__isnull=True).update(left_at=now)
    livekit.end_room(stream.room_name)
    return stream


def invite(stream, host, user_id) -> LiveInvite:
    from django.contrib.auth import get_user_model

    from apps.notifications.models import NotificationType, TargetType
    from apps.notifications.services import notify

    _require_live(stream)
    if stream.host_id != host.pk:
        raise ServiceError("Only the host can invite guests.", code="permission_denied", status_code=403)
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None or user.pk == host.pk or is_blocked_between(host, user):
        raise ServiceError("That person can't be invited.", code="invalid_guest")
    if not active_viewers(stream).filter(user=user).exists():
        raise ServiceError("Only people watching right now can be invited.", code="not_watching")
    if LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.ACCEPTED).count() >= MAX_GUESTS:
        raise ServiceError(f"You can have up to {MAX_GUESTS} guests at a time.", code="guest_limit")
    inv, _ = LiveInvite.objects.update_or_create(
        stream=stream, user=user, defaults={"status": LiveInvite.Status.PENDING}
    )
    notify(
        recipient=user,
        sender=host,
        notification_type=NotificationType.LIVE_INVITE,
        target_type=TargetType.LIVE,
        reference_id=stream.pk,
    )
    return inv


def remove_guest(stream, actor, user_id) -> None:
    """The host takes a guest off screen, or a guest steps down."""
    if actor.pk not in (stream.host_id, int(user_id)):
        raise ServiceError("You can't remove this guest.", code="permission_denied", status_code=403)
    updated = LiveInvite.objects.filter(
        stream=stream, user_id=user_id, status__in=[LiveInvite.Status.ACCEPTED, LiveInvite.Status.PENDING]
    ).update(status=LiveInvite.Status.ENDED)
    if updated:
        livekit.set_can_publish(stream.room_name, str(user_id), False)


# ---------------------------------------------------------------------------
# Viewer actions
# ---------------------------------------------------------------------------
def _require_live(stream):
    if stream.status != LiveStream.Status.LIVE:
        raise ServiceError("This live video has ended.", code="live_ended", status_code=410)


def join(stream, user) -> str:
    require_configured()
    _require_live(stream)
    if stream.host_id == user.pk:
        stream.host_seen_at = timezone.now()
        stream.save(update_fields=["host_seen_at"])
        return "host"
    viewer, created = LiveViewer.objects.get_or_create(stream=stream, user=user)
    if not created:
        viewer.left_at = None
        viewer.save(update_fields=["left_at", "last_seen_at"])
    else:
        LiveStream.objects.filter(pk=stream.pk).update(total_viewers=F("total_viewers") + 1)
    watching = active_viewers(stream).count()
    LiveStream.objects.filter(pk=stream.pk, peak_viewers__lt=watching).update(peak_viewers=watching)
    return role_of(stream, user)


def leave(stream, user) -> None:
    if stream.host_id == user.pk:
        end(stream, user)
        return
    LiveViewer.objects.filter(stream=stream, user=user).update(left_at=timezone.now())
    remove_guest(stream, user, user.pk)


def heartbeat(stream, user) -> None:
    now = timezone.now()
    if stream.host_id == user.pk:
        LiveStream.objects.filter(pk=stream.pk).update(host_seen_at=now)
    else:
        LiveViewer.objects.filter(stream=stream, user=user).update(last_seen_at=now, left_at=None)


def respond_invite(stream, user, accept: bool) -> str:
    _require_live(stream)
    inv = pending_invite_for(stream, user)
    if inv is None:
        raise ServiceError("There is no pending invite.", code="no_invite", status_code=404)
    if accept and LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.ACCEPTED).count() >= MAX_GUESTS:
        raise ServiceError("The guest spots are full.", code="guest_limit")
    inv.status = LiveInvite.Status.ACCEPTED if accept else LiveInvite.Status.DECLINED
    inv.save(update_fields=["status", "updated_at"])
    return "guest" if accept else "viewer"


def comment(stream, user, text) -> LiveComment:
    _require_live(stream)
    text = (text or "").strip()
    if not text:
        raise ServiceError("Write something first.", code="empty_comment")
    c = LiveComment.objects.create(stream=stream, user=user, text=text[:300])
    LiveStream.objects.filter(pk=stream.pk).update(comments_count=F("comments_count") + 1)
    return c


def comments_after(stream, viewer, after_id=0, limit=50):
    qs = LiveComment.objects.filter(stream=stream, user__is_active=True).select_related("user__profile_image")
    qs = exclude_blocked(qs, viewer, field="user_id")
    if after_id:
        return list(qs.filter(pk__gt=after_id).order_by("id")[:limit])
    # First poll: the latest few, oldest first.
    return list(reversed(qs.order_by("-id")[:limit]))
