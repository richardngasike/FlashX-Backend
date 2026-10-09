from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.targets import kind_of, owner_of, preview_of
from apps.notifications.models import NotificationType
from apps.notifications.services import notify, withdraw

from .models import Like


def _notification_type(kind):
    return NotificationType.COMMENT_LIKE if kind == "comment" else NotificationType.LIKE


def _count(target):
    return type(target).objects.filter(pk=target.pk).values_list("likes_count", flat=True).first() or 0


def like(user, target) -> int:
    kind = kind_of(target)
    try:
        with transaction.atomic():
            Like.objects.create(user=user, **{kind: target})
            type(target).objects.filter(pk=target.pk).update(likes_count=F("likes_count") + 1)
            notify(
                recipient=owner_of(target),
                sender=user,
                notification_type=_notification_type(kind),
                target_type=kind,
                reference_id=target.pk,
                preview=preview_of(target),
            )
    except IntegrityError:
        pass  # Already liked: idempotent.
    return _count(target)


def unlike(user, target) -> int:
    kind = kind_of(target)
    with transaction.atomic():
        deleted, _ = Like.objects.filter(user=user, **{kind: target}).delete()
        if deleted:
            type(target).objects.filter(pk=target.pk).update(likes_count=Greatest(F("likes_count") - 1, 0))
            withdraw(sender=user, notification_type=_notification_type(kind), target_type=kind, reference_id=target.pk)
    return _count(target)
