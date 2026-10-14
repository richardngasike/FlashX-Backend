"""
Live video rules. Video itself flows through LiveKit; this module keeps the
stream record, presence, comments, guests, moderation and polls.

Presence is heartbeat based: the app polls ``comments/`` every few seconds,
which refreshes ``last_seen_at`` for viewers and ``host_seen_at`` for the
host. A viewer counts as watching while seen in the last ``VIEWER_WINDOW``;
a stream whose host has been silent for ``HOST_TIMEOUT`` is ended
automatically (app killed, phone lost signal).

Roles in a room:
- host: everything, including ending, pausing, pinning guests, appointing moderators.
- moderator: chosen by the host; manages comments, guest requests, mutes,
  removals and bans (never the host or another moderator's role).
- guest: on screen (publishes audio/video), up to ``max_guests()`` at a time.
- viewer: watches, comments, reacts, votes, asks to join.

Floating reactions travel over LiveKit's data channel between devices; the
API only counts them (``add_reactions``) for the host's analytics.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from apps.blocks.selectors import exclude_blocked, hidden_user_ids, is_blocked_between
from apps.core.exceptions import ServiceError
from apps.notifications import sync

from . import livekit
from .models import (
    LiveBan,
    LiveComment,
    LiveInvite,
    LiveModerationLog,
    LiveModerator,
    LivePoll,
    LivePollVote,
    LiveStream,
    LiveViewer,
)

logger = logging.getLogger(__name__)

VIEWER_WINDOW = timedelta(seconds=45)
HOST_TIMEOUT = timedelta(seconds=90)
FOLLOWER_PUSH_LIMIT = 200
MAX_POLL_OPTIONS = 6
MAX_REACTIONS_PER_CALL = 50
CATEGORIES = (
    "chat",
    "music",
    "gaming",
    "education",
    "sports",
    "fitness",
    "food",
    "travel",
    "art",
    "tech",
    "business",
    "news",
    "comedy",
    "beauty",
    "other",
)


def max_guests() -> int:
    """Guests on screen besides the host (99 = rooms of up to 100 people)."""
    return int(getattr(settings, "LIVE_MAX_GUESTS", 99))


class LiveUnavailable(ServiceError):
    status_code = 503
    default_code = "live_unavailable"
    default_detail = "Live video is not available right now."


def require_configured():
    if not livekit.is_configured():
        raise LiveUnavailable()


def _user_model():
    from django.contrib.auth import get_user_model

    return get_user_model()


def _emit(user_ids, stream, reason, **data):
    """Silent "refresh the live room" signal (their app also polls every few seconds)."""
    sync.emit(
        user_ids, "live", collapse_key=f"live:{stream.pk}:{reason}", stream_id=str(stream.pk), reason=reason, **data
    )


def _log(stream, actor, action, target=None, detail=""):
    LiveModerationLog.objects.create(
        stream=stream, actor=actor, action=action, target_user=target, detail=(detail or "")[:300]
    )


# ---------------------------------------------------------------------------
# Reads and permissions
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
    LivePoll.objects.filter(stream_id__in=ids, is_open=True).update(is_open=False, closed_at=now)
    for _, room in stale:
        livekit.end_room(room)
    return len(ids)


def _audience_filter(viewer):
    """Streams this viewer may see, by the host's visibility choice."""
    from apps.follows.models import Follow

    follows = Follow.objects.filter(follower=viewer, following_id=OuterRef("host_id"))
    return (
        Q(visibility=LiveStream.Visibility.PUBLIC)
        | (Q(visibility=LiveStream.Visibility.FOLLOWERS) & Q(Exists(follows)))
        | Q(visibility=LiveStream.Visibility.PRIVATE, allowed_users=viewer)
    )


def live_now(viewer):
    """Streams on air that the viewer may watch, people they follow first."""
    from apps.follows.models import Follow

    expire_stale()
    qs = (
        LiveStream.objects.filter(status=LiveStream.Status.LIVE, host__is_active=True)
        .exclude(host=viewer)
        .filter(_audience_filter(viewer))
        .exclude(bans__user=viewer)
        .select_related("host__profile_image")
        .annotate(followed=Exists(Follow.objects.filter(follower=viewer, following_id=OuterRef("host_id"))))
        .distinct()
    )
    qs = exclude_blocked(qs, viewer, field="host_id")
    return with_viewer_count(qs).order_by("-followed", "-started_at")


def can_watch(stream, user) -> bool:
    if stream.host_id == user.pk:
        return True
    if stream.visibility == LiveStream.Visibility.PUBLIC:
        return True
    if stream.visibility == LiveStream.Visibility.FOLLOWERS:
        from apps.follows.models import Follow

        return Follow.objects.filter(follower=user, following_id=stream.host_id).exists()
    return stream.allowed_users.filter(pk=user.pk).exists()


def is_banned(stream, user) -> bool:
    return LiveBan.objects.filter(stream=stream, user=user).exists()


def get_stream(pk, viewer) -> LiveStream:
    stream = with_viewer_count(LiveStream.objects.select_related("host__profile_image")).filter(pk=pk).first()
    if (
        stream is None
        or not stream.host.is_active
        or is_blocked_between(viewer, stream.host)
        or not can_watch(stream, viewer)
    ):
        raise ServiceError("Live video not found.", code="not_found", status_code=404)
    if is_banned(stream, viewer):
        raise ServiceError("You were removed from this live video.", code="live_banned", status_code=403)
    return stream


def is_moderator(stream, user) -> bool:
    return LiveModerator.objects.filter(stream=stream, user=user).exists()


def can_manage(stream, user) -> bool:
    return stream.host_id == user.pk or is_moderator(stream, user)


def _require_host(stream, user, message="Only the host can do this."):
    if stream.host_id != user.pk:
        raise ServiceError(message, code="permission_denied", status_code=403)


def _require_manager(stream, user):
    if not can_manage(stream, user):
        raise ServiceError("Only the host or a moderator can do this.", code="permission_denied", status_code=403)


def role_of(stream, user) -> str:
    if stream.host_id == user.pk:
        return "host"
    if LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.ACCEPTED).exists():
        return "guest"
    return "viewer"


def guest_invites(stream):
    return list(
        LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.ACCEPTED)
        .select_related("user__profile_image")
        .order_by("updated_at", "id")
    )


def guests(stream):
    return [i.user for i in guest_invites(stream)]


def pending_invite_for(stream, user):
    return LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.PENDING).first()


def pending_requests(stream):
    return list(
        LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.REQUESTED, user__is_active=True)
        .select_related("user__profile_image")
        .order_by("updated_at", "id")
    )


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
def _clean_meta(title=None, description=None, category=None):
    out = {}
    if title is not None:
        out["title"] = (title or "").strip()[:120]
    if description is not None:
        out["description"] = (description or "").strip()[:500]
    if category is not None:
        category = (category or "").strip().lower()
        if category and category not in CATEGORIES:
            raise ServiceError("Choose a category from the list.", code="invalid_category")
        out["category"] = category
    return out


def _allowed_users(host, user_ids):
    ids = {int(i) for i in (user_ids or [])} - {host.pk}
    if not ids:
        return []
    hidden = hidden_user_ids(host)
    return list(_user_model().objects.filter(pk__in=ids - hidden, is_active=True))


def start(
    host,
    title="",
    *,
    description="",
    category="",
    visibility=LiveStream.Visibility.PUBLIC,
    allowed_user_ids=None,
    audio_only=False,
    allow_requests=True,
    notify_followers=True,
) -> LiveStream:
    require_configured()
    meta = _clean_meta(title, description, category)
    if visibility not in LiveStream.Visibility.values:
        raise ServiceError("Choose who can watch.", code="invalid_visibility")
    allowed = _allowed_users(host, allowed_user_ids) if visibility == LiveStream.Visibility.PRIVATE else []
    if visibility == LiveStream.Visibility.PRIVATE and not allowed:
        raise ServiceError("Choose at least one person for a private live.", code="no_audience")
    # One stream per host: starting again replaces a stream left open.
    for old in LiveStream.objects.filter(host=host, status=LiveStream.Status.LIVE):
        end(old, host)
    with transaction.atomic():
        stream = LiveStream.objects.create(
            host=host,
            visibility=visibility,
            audio_only=bool(audio_only),
            allow_requests=bool(allow_requests),
            host_seen_at=timezone.now(),
            **meta,
        )
        if allowed:
            stream.allowed_users.set(allowed)
        notification_ids = _notify_audience(stream, allowed) if notify_followers or allowed else []
    if notification_ids:
        transaction.on_commit(lambda: _push_many(notification_ids))
    return stream


def _notify_audience(stream, allowed) -> list:
    """Store a "went live" notification for followers (or the chosen people). Returns their ids for push."""
    from apps.follows.models import Follow
    from apps.notifications.models import Notification, NotificationType, TargetType

    if stream.visibility == LiveStream.Visibility.PRIVATE:
        recipient_ids = [u.pk for u in allowed][:FOLLOWER_PUSH_LIMIT]
    else:
        hidden = hidden_user_ids(stream.host)
        recipient_ids = list(
            Follow.objects.filter(following=stream.host, follower__is_active=True)
            .exclude(follower_id__in=hidden)
            .order_by("-created_at")
            .values_list("follower_id", flat=True)[:FOLLOWER_PUSH_LIMIT]
        )
    if not recipient_ids:
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
            for uid in recipient_ids
        ]
    )
    return [n.pk for n in created if n.pk]


def _push_many(notification_ids):
    from apps.notifications import push

    if push.is_enabled():
        push.dispatch_many(notification_ids)


def update_details(stream, host, *, title=None, description=None, category=None, allow_requests=None):
    _require_host(stream, host)
    _require_live(stream)
    fields = _clean_meta(title, description, category)
    if allow_requests is not None:
        fields["allow_requests"] = bool(allow_requests)
        if not allow_requests:
            LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.REQUESTED).update(
                status=LiveInvite.Status.DECLINED
            )
    for k, v in fields.items():
        setattr(stream, k, v)
    if fields:
        stream.save(update_fields=list(fields))
    return stream


def set_paused(stream, host, paused: bool):
    """The host steps away: their video and sound stop and viewers see a "paused" screen."""
    _require_host(stream, host)
    _require_live(stream)
    if paused and stream.paused_at is None:
        stream.paused_at = timezone.now()
    elif not paused:
        stream.paused_at = None
    stream.save(update_fields=["paused_at"])
    return stream


def end(stream, user) -> LiveStream:
    if stream.host_id != user.pk:
        raise ServiceError("Only the host can end this live video.", code="permission_denied", status_code=403)
    if stream.status == LiveStream.Status.ENDED:
        return stream
    now = timezone.now()
    stream.status = LiveStream.Status.ENDED
    stream.ended_at = now
    stream.paused_at = None
    stream.save(update_fields=["status", "ended_at", "paused_at"])
    stream.invites.exclude(status=LiveInvite.Status.DECLINED).update(status=LiveInvite.Status.ENDED)
    stream.viewers.filter(left_at__isnull=True).update(left_at=now)
    stream.polls.filter(is_open=True).update(is_open=False, closed_at=now)
    livekit.end_room(stream.room_name)
    return stream


def _guest_slots_left(stream) -> int:
    return max_guests() - LiveInvite.objects.filter(stream=stream, status=LiveInvite.Status.ACCEPTED).count()


def invite(stream, actor, user_id) -> LiveInvite:
    from apps.notifications.models import NotificationType, TargetType
    from apps.notifications.services import notify

    _require_live(stream)
    _require_manager(stream, actor)
    user = _user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None or user.pk == stream.host_id or is_blocked_between(stream.host, user) or is_banned(stream, user):
        raise ServiceError("That person can't be invited.", code="invalid_guest")
    if not active_viewers(stream).filter(user=user).exists():
        raise ServiceError("Only people watching right now can be invited.", code="not_watching")
    if _guest_slots_left(stream) <= 0:
        raise ServiceError(f"You can have up to {max_guests()} guests at a time.", code="guest_limit")
    current = LiveInvite.objects.filter(stream=stream, user=user).first()
    if current and current.status == LiveInvite.Status.ACCEPTED:
        return current
    if current and current.status == LiveInvite.Status.REQUESTED:
        # They already asked: inviting them is the same as accepting.
        return respond_request(stream, actor, user.pk, True)
    inv, _ = LiveInvite.objects.update_or_create(
        stream=stream, user=user, defaults={"status": LiveInvite.Status.PENDING, "muted_by_host": False}
    )
    notify(
        recipient=user,
        sender=stream.host,
        notification_type=NotificationType.LIVE_INVITE,
        target_type=TargetType.LIVE,
        reference_id=stream.pk,
    )
    _emit([user.pk], stream, "invited")
    return inv


def remove_guest(stream, actor, user_id, *, log=True) -> None:
    """The host or a moderator takes a guest off screen, or a guest steps down."""
    user_id = int(user_id)
    is_self = actor.pk == user_id
    if not is_self:
        _require_manager(stream, actor)
        if user_id == stream.host_id:
            raise ServiceError("The host can't be removed.", code="permission_denied", status_code=403)
    updated = LiveInvite.objects.filter(
        stream=stream,
        user_id=user_id,
        status__in=[LiveInvite.Status.ACCEPTED, LiveInvite.Status.PENDING, LiveInvite.Status.REQUESTED],
    ).update(status=LiveInvite.Status.ENDED, muted_by_host=False)
    if stream.pinned_user_id == user_id:
        LiveStream.objects.filter(pk=stream.pk).update(pinned_user=None)
    if updated:
        livekit.set_can_publish(stream.room_name, str(user_id), False)
        if not is_self:
            if log:
                _log(stream, actor, "remove_guest", _user_model().objects.filter(pk=user_id).first())
            _emit([user_id], stream, "removed")


def mute_guest(stream, actor, user_id, muted: bool) -> LiveInvite:
    """Turn a guest's microphone off (or allow it again). Their camera stays on."""
    _require_live(stream)
    _require_manager(stream, actor)
    inv = (
        LiveInvite.objects.filter(stream=stream, user_id=user_id, status=LiveInvite.Status.ACCEPTED)
        .select_related("user")
        .first()
    )
    if inv is None:
        raise ServiceError("That person isn't on screen.", code="not_a_guest", status_code=404)
    if inv.muted_by_host != muted:
        inv.muted_by_host = muted
        inv.save(update_fields=["muted_by_host", "updated_at"])
        livekit.set_can_publish(stream.room_name, str(inv.user_id), True, microphone=not muted)
        _log(stream, actor, "mute" if muted else "unmute", inv.user)
        _emit([inv.user_id], stream, "muted" if muted else "unmuted")
    return inv


def pin_user(stream, host, user_id=None) -> LiveStream:
    """Show one guest (or the host, with None) in the large view for everyone."""
    _require_host(stream, host)
    _require_live(stream)
    if user_id and int(user_id) != stream.host_id:
        if not LiveInvite.objects.filter(stream=stream, user_id=user_id, status=LiveInvite.Status.ACCEPTED).exists():
            raise ServiceError("Only guests on screen can be pinned.", code="not_a_guest")
        stream.pinned_user_id = int(user_id)
    else:
        stream.pinned_user_id = None
    stream.save(update_fields=["pinned_user"])
    return stream


def set_moderator(stream, host, user_id, on: bool):
    _require_host(stream, host, "Only the host can choose moderators.")
    user = _user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None or user.pk == stream.host_id or is_blocked_between(stream.host, user):
        raise ServiceError("That person can't be a moderator.", code="invalid_moderator")
    if on:
        _, created = LiveModerator.objects.get_or_create(stream=stream, user=user)
    else:
        created = bool(LiveModerator.objects.filter(stream=stream, user=user).delete()[0])
    if created:
        _log(stream, host, "add_moderator" if on else "remove_moderator", user)
        _emit([user.pk], stream, "moderator")


def ban(stream, actor, user_id):
    """Remove someone from the live and keep them out of it."""
    _require_manager(stream, actor)
    user = _user_model().objects.filter(pk=user_id).first()
    if user is None or user.pk == actor.pk:
        raise ServiceError("That person can't be removed.", code="invalid_target")
    if user.pk == stream.host_id or (actor.pk != stream.host_id and is_moderator(stream, user)):
        raise ServiceError("You can't remove this person.", code="permission_denied", status_code=403)
    with transaction.atomic():
        _, created = LiveBan.objects.get_or_create(stream=stream, user=user, defaults={"banned_by": actor})
        LiveModerator.objects.filter(stream=stream, user=user).delete()
        remove_guest(stream, actor, user.pk, log=False)
        LiveViewer.objects.filter(stream=stream, user=user).update(left_at=timezone.now())
        if created:
            _log(stream, actor, "ban", user)
    livekit.remove_participant(stream.room_name, str(user.pk))
    _emit([user.pk], stream, "banned")


def unban(stream, actor, user_id):
    _require_manager(stream, actor)
    deleted = LiveBan.objects.filter(stream=stream, user_id=user_id).delete()[0]
    if deleted:
        _log(stream, actor, "unban", _user_model().objects.filter(pk=user_id).first())


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
    if accept and _guest_slots_left(stream) <= 0:
        raise ServiceError("The guest spots are full.", code="guest_limit")
    inv.status = LiveInvite.Status.ACCEPTED if accept else LiveInvite.Status.DECLINED
    inv.save(update_fields=["status", "updated_at"])
    _emit([stream.host_id], stream, "invite_accepted" if accept else "invite_declined", user_id=str(user.pk))
    return "guest" if accept else "viewer"


def request_to_join(stream, user) -> LiveInvite:
    """A viewer asks to come on screen; the host and moderators see it in their request queue."""
    _require_live(stream)
    if stream.host_id == user.pk:
        raise ServiceError("You're the host.", code="invalid_request")
    if not stream.allow_requests:
        raise ServiceError("The host isn't taking requests right now.", code="requests_off")
    if not active_viewers(stream).filter(user=user).exists():
        raise ServiceError("Open the live video first.", code="not_watching")
    current = LiveInvite.objects.filter(stream=stream, user=user).first()
    if current and current.status == LiveInvite.Status.ACCEPTED:
        return current
    if current and current.status == LiveInvite.Status.PENDING:
        # The host already invited them: asking is accepting.
        respond_invite(stream, user, True)
        current.refresh_from_db()
        return current
    inv, _ = LiveInvite.objects.update_or_create(
        stream=stream, user=user, defaults={"status": LiveInvite.Status.REQUESTED, "muted_by_host": False}
    )
    managers = [stream.host_id, *LiveModerator.objects.filter(stream=stream).values_list("user_id", flat=True)]
    _emit(managers, stream, "request", user_id=str(user.pk))
    return inv


def cancel_request(stream, user) -> None:
    LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.REQUESTED).update(
        status=LiveInvite.Status.ENDED
    )


def respond_request(stream, actor, user_id, accept: bool) -> LiveInvite:
    _require_live(stream)
    _require_manager(stream, actor)
    inv = (
        LiveInvite.objects.filter(stream=stream, user_id=user_id, status=LiveInvite.Status.REQUESTED)
        .select_related("user")
        .first()
    )
    if inv is None:
        raise ServiceError("That request is no longer waiting.", code="no_request", status_code=404)
    if accept and _guest_slots_left(stream) <= 0:
        raise ServiceError(f"You can have up to {max_guests()} guests at a time.", code="guest_limit")
    inv.status = LiveInvite.Status.ACCEPTED if accept else LiveInvite.Status.DECLINED
    inv.save(update_fields=["status", "updated_at"])
    if accept:
        # Lets them publish without reconnecting; their app also rejoins with a guest token.
        livekit.set_can_publish(stream.room_name, str(inv.user_id), True)
    _log(stream, actor, "accept_request" if accept else "reject_request", inv.user)
    _emit([inv.user_id], stream, "request_accepted" if accept else "request_declined")
    return inv


# ---------------------------------------------------------------------------
# Comments, questions, polls, reactions
# ---------------------------------------------------------------------------
def comment(stream, user, text, *, reply_to_id=None, kind=LiveComment.Kind.COMMENT) -> LiveComment:
    _require_live(stream)
    if is_banned(stream, user):
        raise ServiceError("You were removed from this live video.", code="live_banned", status_code=403)
    text = (text or "").strip()
    if not text:
        raise ServiceError("Write something first.", code="empty_comment")
    if kind not in (LiveComment.Kind.COMMENT, LiveComment.Kind.QUESTION):
        kind = LiveComment.Kind.COMMENT
    reply_to = None
    if reply_to_id:
        reply_to = LiveComment.objects.filter(pk=reply_to_id, stream=stream, is_removed=False).first()
        if reply_to is None:
            raise ServiceError("That comment is no longer available.", code="reply_not_found")
    c = LiveComment.objects.create(stream=stream, user=user, text=text[:300], kind=kind, reply_to=reply_to)
    LiveStream.objects.filter(pk=stream.pk).update(comments_count=F("comments_count") + 1)
    return c


def _comment_in(stream, comment_id) -> LiveComment:
    c = LiveComment.objects.filter(pk=comment_id, stream=stream).select_related("user").first()
    if c is None:
        raise ServiceError("Comment not found.", code="not_found", status_code=404)
    return c


def remove_comment(stream, actor, comment_id) -> None:
    c = _comment_in(stream, comment_id)
    if c.user_id != actor.pk:
        _require_manager(stream, actor)
    if c.is_removed:
        return
    c.is_removed = True
    c.save(update_fields=["is_removed"])
    if stream.pinned_comment_id == c.pk:
        LiveStream.objects.filter(pk=stream.pk).update(pinned_comment=None)
    if c.user_id != actor.pk:
        _log(stream, actor, "remove_comment", c.user, c.text)


def pin_comment(stream, actor, comment_id=None) -> LiveStream:
    _require_manager(stream, actor)
    if comment_id:
        c = _comment_in(stream, comment_id)
        if c.is_removed:
            raise ServiceError("That comment was removed.", code="not_found", status_code=404)
        stream.pinned_comment = c
    else:
        stream.pinned_comment = None
    stream.save(update_fields=["pinned_comment"])
    return stream


def mark_answered(stream, actor, comment_id, answered=True) -> LiveComment:
    _require_manager(stream, actor)
    c = _comment_in(stream, comment_id)
    c.answered = bool(answered)
    c.save(update_fields=["answered"])
    return c


def comments_after(stream, viewer, after_id=0, limit=50):
    qs = LiveComment.objects.filter(stream=stream, user__is_active=True, is_removed=False).select_related(
        "user__profile_image", "reply_to__user", "gift_send__gift", "gift_send__recipient"
    )
    qs = exclude_blocked(qs, viewer, field="user_id")
    if after_id:
        return list(qs.filter(pk__gt=after_id).order_by("id")[:limit])
    # First poll: the latest few, oldest first.
    return list(reversed(qs.order_by("-id")[:limit]))


def removed_comment_ids(stream, limit=100) -> list:
    return list(
        LiveComment.objects.filter(stream=stream, is_removed=True).order_by("-id").values_list("id", flat=True)[:limit]
    )


def open_questions(stream, limit=50):
    return list(
        LiveComment.objects.filter(
            stream=stream, kind=LiveComment.Kind.QUESTION, is_removed=False, answered=False, user__is_active=True
        )
        .select_related("user__profile_image")
        .order_by("id")[:limit]
    )


def create_poll(stream, actor, question, options) -> LivePoll:
    _require_live(stream)
    _require_manager(stream, actor)
    question = (question or "").strip()[:140]
    options = [str(o).strip()[:60] for o in (options or []) if str(o).strip()]
    options = list(dict.fromkeys(options))
    if not question or not (2 <= len(options) <= MAX_POLL_OPTIONS):
        raise ServiceError(
            f"A poll needs a question and 2 to {MAX_POLL_OPTIONS} different options.", code="invalid_poll"
        )
    now = timezone.now()
    with transaction.atomic():
        stream.polls.filter(is_open=True).update(is_open=False, closed_at=now)
        poll = LivePoll.objects.create(stream=stream, created_by=actor, question=question, options=options)
    _log(stream, actor, "poll", detail=question)
    return poll


def vote(stream, user, poll_id, option: int) -> LivePoll:
    _require_live(stream)
    if is_banned(stream, user):
        raise ServiceError("You were removed from this live video.", code="live_banned", status_code=403)
    poll = LivePoll.objects.filter(pk=poll_id, stream=stream).first()
    if poll is None:
        raise ServiceError("Poll not found.", code="not_found", status_code=404)
    if not poll.is_open:
        raise ServiceError("This poll has closed.", code="poll_closed")
    if not 0 <= int(option) < len(poll.options):
        raise ServiceError("Choose one of the options.", code="invalid_option")
    LivePollVote.objects.update_or_create(poll=poll, user=user, defaults={"option": int(option)})
    return poll


def close_poll(stream, actor, poll_id) -> LivePoll:
    _require_manager(stream, actor)
    poll = LivePoll.objects.filter(pk=poll_id, stream=stream).first()
    if poll is None:
        raise ServiceError("Poll not found.", code="not_found", status_code=404)
    if poll.is_open:
        poll.is_open = False
        poll.closed_at = timezone.now()
        poll.save(update_fields=["is_open", "closed_at"])
    return poll


def current_poll(stream):
    """The open poll, or the last one closed in the past minute (so results stay on screen briefly)."""
    recent = timezone.now() - timedelta(seconds=60)
    return stream.polls.filter(Q(is_open=True) | Q(closed_at__gte=recent)).order_by("-is_open", "-created_at").first()


def poll_payload(poll, user) -> dict | None:
    if poll is None:
        return None
    counts = dict(poll.votes.values_list("option").annotate(n=Count("id")))
    total = sum(counts.values())
    mine = poll.votes.filter(user=user).values_list("option", flat=True).first()
    return {
        "id": poll.pk,
        "question": poll.question,
        "options": [
            {
                "index": i,
                "text": text,
                "votes": counts.get(i, 0),
                "percent": round(100 * counts.get(i, 0) / total) if total else 0,
            }
            for i, text in enumerate(poll.options)
        ],
        "total_votes": total,
        "my_vote": mine,
        "is_open": poll.is_open,
    }


def add_reactions(stream, user, count: int) -> int:
    """Count reactions for analytics (the animation itself goes device-to-device over LiveKit)."""
    _require_live(stream)
    count = max(0, min(int(count or 0), MAX_REACTIONS_PER_CALL))
    if count:
        LiveStream.objects.filter(pk=stream.pk).update(reactions_count=F("reactions_count") + count)
    return count


# ---------------------------------------------------------------------------
# Analytics and reporting
# ---------------------------------------------------------------------------
def stats(stream, host) -> dict:
    _require_host(stream, host, "Only the host can see these numbers.")
    from apps.follows.models import Follow

    end_at = stream.ended_at or timezone.now()
    duration = max(0, int((end_at - stream.started_at).total_seconds()))
    watch = []
    for joined, last, left in stream.viewers.exclude(user_id=stream.host_id).values_list(
        "joined_at", "last_seen_at", "left_at"
    ):
        stop = min(left or last, end_at)
        watch.append(max(0, (stop - joined).total_seconds()))
    return {
        "duration_seconds": duration,
        "peak_viewers": stream.peak_viewers,
        "total_viewers": stream.total_viewers,
        "comments": stream.comments_count,
        "reactions": stream.reactions_count,
        "gifts_coins": stream.gifts_coins,
        "gifts": stream.gifts.count(),
        "top_gifters": top_gifters(stream),
        "questions": stream.comments.filter(kind=LiveComment.Kind.QUESTION).count(),
        "guests": stream.invites.filter(
            Q(status=LiveInvite.Status.ACCEPTED) | Q(status=LiveInvite.Status.ENDED)
        ).count(),
        "polls": stream.polls.count(),
        "new_followers": Follow.objects.filter(
            following_id=stream.host_id, created_at__gte=stream.started_at, created_at__lte=end_at
        ).count(),
        "average_watch_seconds": int(sum(watch) / len(watch)) if watch else 0,
    }


def report(stream, reporter, reason, details=""):
    """Reports go to the moderation queue against the host, tagged with the live."""
    from apps.reports.services import file_report

    if stream.host_id == reporter.pk:
        raise ServiceError("You cannot report your own live video.", code="self_report")
    note = f"[Live #{stream.pk}: {stream.title or 'untitled'}] {(details or '').strip()}"
    return file_report(reporter, target_type="user", target_id=stream.host_id, reason=reason, details=note[:1000])


def top_gifters(stream, limit=5) -> list:
    """Who sent the most coins in gifts during a live."""
    from django.db.models import Sum

    User = _user_model()
    rows = list(stream.gifts.values("sender_id").annotate(total=Sum("coins")).order_by("-total", "sender_id")[:limit])
    users = {u.pk: u for u in User.objects.filter(pk__in=[r["sender_id"] for r in rows])}
    return [
        {"user_id": r["sender_id"], "username": users[r["sender_id"]].username, "coins": r["total"]}
        for r in rows
        if r["sender_id"] in users
    ]
