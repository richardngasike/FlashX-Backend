from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.exceptions import ServiceError
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify, withdraw

from .models import Follow, FollowRequest

User = get_user_model()


def follow(follower, target) -> bool:
    """Returns True if a new follow was created. Private accounts get a request instead (see request_follow)."""
    if follower.pk == target.pk:
        raise ServiceError("You cannot follow yourself.", code="self_follow")
    if not target.is_active:
        raise ServiceError("This account is unavailable.", code="user_unavailable", status_code=404)
    from apps.blocks.services import ensure_not_blocked

    ensure_not_blocked(follower, target, "Unblock this account to follow it.")
    return _create_follow(follower, target)


def follow_or_request(follower, target) -> tuple[str, bool]:
    """Follow, or ask to follow a private account. Returns ("following" | "requested", created)."""
    if follower.pk == target.pk:
        raise ServiceError("You cannot follow yourself.", code="self_follow")
    if not target.is_active:
        raise ServiceError("This account is unavailable.", code="user_unavailable", status_code=404)
    from apps.blocks.services import ensure_not_blocked

    ensure_not_blocked(follower, target, "Unblock this account to follow it.")
    if Follow.objects.filter(follower=follower, following=target).exists():
        return "following", False
    if not target.is_private:
        return "following", _create_follow(follower, target)
    _, created = FollowRequest.objects.get_or_create(requester=follower, target=target)
    if created:
        notify(
            recipient=target,
            sender=follower,
            notification_type=NotificationType.FOLLOW_REQUEST,
            target_type=TargetType.USER,
            reference_id=follower.pk,
        )
    return "requested", created


def cancel_request(requester, target) -> bool:
    deleted, _ = FollowRequest.objects.filter(requester=requester, target=target).delete()
    if deleted:
        withdraw(
            sender=requester,
            notification_type=NotificationType.FOLLOW_REQUEST,
            target_type=TargetType.USER,
            reference_id=requester.pk,
            recipient=target,
        )
    return bool(deleted)


def approve_request(target, requester_id) -> bool:
    req = FollowRequest.objects.filter(target=target, requester_id=requester_id).select_related("requester").first()
    if req is None:
        raise ServiceError("Follow request not found.", code="not_found", status_code=404)
    requester = req.requester
    with transaction.atomic():
        req.delete()
        withdraw(
            sender=requester,
            notification_type=NotificationType.FOLLOW_REQUEST,
            target_type=TargetType.USER,
            reference_id=requester.pk,
            recipient=target,
        )
        if requester.is_active:
            _create_follow(requester, target, notify_target=False)
            notify(
                recipient=requester,
                sender=target,
                notification_type=NotificationType.FOLLOW_ACCEPTED,
                target_type=TargetType.USER,
                reference_id=target.pk,
            )
    return True


def decline_request(target, requester_id) -> bool:
    req = FollowRequest.objects.filter(target=target, requester_id=requester_id).select_related("requester").first()
    if req is None:
        return False
    return cancel_request(req.requester, target)


def approve_all_requests(target) -> int:
    ids = list(FollowRequest.objects.filter(target=target).values_list("requester_id", flat=True))
    for rid in ids:
        approve_request(target, rid)
    return len(ids)


def _create_follow(follower, target, notify_target=True) -> bool:
    try:
        with transaction.atomic():
            Follow.objects.create(follower=follower, following=target)
            User.objects.filter(pk=follower.pk).update(following_count=F("following_count") + 1)
            User.objects.filter(pk=target.pk).update(followers_count=F("followers_count") + 1)
            if notify_target:
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
