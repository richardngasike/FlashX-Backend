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


def ensure_not_blocked(a, b, message="You can't interact with this account."):
    """Raise when either user has blocked the other."""
    from .selectors import is_blocked_between

    if is_blocked_between(a, b):
        raise ServiceError(message, code="blocked", status_code=403)
