from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.core.exceptions import ServiceError
from apps.notifications.models import Notification

from .models import Block


def block(blocker, target) -> bool:
    """Block ``target``. Removes follows both ways and their shared notifications. True if newly blocked."""
    from apps.follows.services import unfollow

    if blocker.pk == target.pk:
        raise ServiceError("You cannot block yourself.", code="self_block")
    try:
        with transaction.atomic():
            Block.objects.create(blocker=blocker, blocked=target)
            unfollow(blocker, target)
            unfollow(target, blocker)
            Notification.objects.filter(
                Q(recipient=blocker, sender=target) | Q(recipient=target, sender=blocker)
            ).delete()
    except IntegrityError:
        return False
    return True


def unblock(blocker, target) -> bool:
    deleted, _ = Block.objects.filter(blocker=blocker, blocked=target).delete()
    return bool(deleted)


UNAVAILABLE_MESSAGE = "This account isn't available."


def unavailable():
    """The error a blocked person gets: identical to a deleted or deactivated account, so the block stays private."""
    return ServiceError(UNAVAILABLE_MESSAGE, code="user_unavailable", status_code=404)


def ensure_not_blocked(actor, target, message="Unblock this account to continue."):
    """
    Raise when either user has blocked the other. The person who blocked gets
    ``message`` (they know); the blocked person gets the generic "unavailable"
    error, never a hint that they were blocked.
    """
    if actor is None or target is None or actor.pk == target.pk:
        return
    rows = set(
        Block.objects.filter(Q(blocker=actor, blocked=target) | Q(blocker=target, blocked=actor)).values_list(
            "blocker_id", flat=True
        )
    )
    if actor.pk in rows:
        raise ServiceError(message, code="blocked", status_code=403)
    if target.pk in rows:
        raise unavailable()
