from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.exceptions import ServiceError
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify, withdraw

from .models import Follow

User = get_user_model()


def follow(follower, target) -> bool:
    """Returns True if a new follow was created."""
    if follower.pk == target.pk:
        raise ServiceError("You cannot follow yourself.", code="self_follow")
    if not target.is_active:
        raise ServiceError("This account is unavailable.", code="user_unavailable", status_code=404)
    from apps.blocks.services import ensure_not_blocked

    ensure_not_blocked(follower, target, "You can't follow this account.")
    try:
        with transaction.atomic():
            Follow.objects.create(follower=follower, following=target)
            User.objects.filter(pk=follower.pk).update(following_count=F("following_count") + 1)
            User.objects.filter(pk=target.pk).update(followers_count=F("followers_count") + 1)
            notify(
                recipient=target,
                sender=follower,
                notification_type=NotificationType.FOLLOW,
                target_type=TargetType.USER,
                reference_id=follower.pk,
            )
    except IntegrityError:
        return False
    return True


def unfollow(follower, target) -> bool:
    with transaction.atomic():
        deleted, _ = Follow.objects.filter(follower=follower, following=target).delete()
        if deleted:
            User.objects.filter(pk=follower.pk).update(following_count=Greatest(F("following_count") - 1, 0))
            User.objects.filter(pk=target.pk).update(followers_count=Greatest(F("followers_count") - 1, 0))
            withdraw(
                sender=follower,
                notification_type=NotificationType.FOLLOW,
                target_type=TargetType.USER,
                reference_id=follower.pk,
                recipient=target,
            )
    return bool(deleted)


def is_following(follower, target) -> bool:
    if not follower or not follower.is_authenticated:
        return False
    return Follow.objects.filter(follower=follower, following=target).exists()


def following_ids_subquery(user):
    return Follow.objects.filter(follower=user).values("following_id")
