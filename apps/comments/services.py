from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.exceptions import ServiceError
from apps.core.targets import kind_of
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify, notify_mentions, purge_target

from .models import Comment


@transaction.atomic
def create_comment(user, target, content, parent_id=None) -> Comment:
    kind = kind_of(target)
    if not target.comments_enabled:
        raise ServiceError("Comments are turned off for this content.", code="comments_disabled", status_code=403)
    parent = None
    if parent_id:
        parent = (
            Comment.objects.select_for_update()
            .filter(pk=parent_id, is_hidden=False, **{kind: target})
            .select_related("user")
            .first()
        )
        if parent is None:
            raise ServiceError(
                "The comment you are replying to no longer exists.", code="parent_not_found", status_code=404
            )
        # Replies stay one level deep: a reply to a reply hangs off the root.
        if parent.parent_comment_id:
            reply_target_user = parent.user
            parent = Comment.objects.select_for_update().select_related("user").get(pk=parent.parent_comment_id)
        else:
            reply_target_user = parent.user

    comment = Comment.objects.create(user=user, content=content, parent_comment=parent, **{kind: target})
    type(target).objects.filter(pk=target.pk).update(comments_count=F("comments_count") + 1)

    notified = {user.pk}
    if parent is not None:
        Comment.objects.filter(pk=parent.pk).update(replies_count=F("replies_count") + 1)
        notify(
            recipient=reply_target_user,
            sender=user,
            notification_type=NotificationType.REPLY,
            target_type=TargetType.COMMENT,
            reference_id=comment.pk,
            preview=content,
        )
        notified.add(reply_target_user.pk)
    if target.author_id not in notified:
        notify(
            recipient=target.author,
            sender=user,
            notification_type=NotificationType.COMMENT,
            target_type=TargetType.COMMENT,
            reference_id=comment.pk,
            preview=content,
        )
        notified.add(target.author_id)
    notify_mentions(
        text=content, sender=user, target_type=TargetType.COMMENT, reference_id=comment.pk, exclude_ids=notified
    )
    return comment


def can_delete(user, comment) -> bool:
    target = comment.post or comment.reel
    return user.pk == comment.user_id or (target is not None and user.pk == target.author_id) or user.is_staff


@transaction.atomic
def delete_comment(user, comment):
    if not can_delete(user, comment):
        raise ServiceError("You can only delete your own comments.", code="permission_denied", status_code=403)
    target = comment.post or comment.reel
    reply_ids = list(comment.replies.values_list("id", flat=True))
    removed = 1 + len(reply_ids)
    if target is not None:
        type(target).objects.filter(pk=target.pk).update(comments_count=Greatest(F("comments_count") - removed, 0))
    if comment.parent_comment_id:
        Comment.objects.filter(pk=comment.parent_comment_id).update(replies_count=Greatest(F("replies_count") - 1, 0))
    purge_target(TargetType.COMMENT, [comment.pk, *reply_ids])
    comment.delete()
