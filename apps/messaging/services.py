from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.blocks.selectors import hidden_user_ids, is_blocked_between
from apps.blocks.services import ensure_not_blocked
from apps.core.exceptions import ServiceError
from apps.media.models import MediaPurpose
from apps.media.services import claim_one, release_asset
from apps.notifications.models import Notification, NotificationType, TargetType
from apps.notifications.services import notify

from .models import Conversation, ConversationParticipant, Message

User = get_user_model()
MAX_GROUP_SIZE = 32


def _active_user(user_id):
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ServiceError("This account is unavailable.", code="user_unavailable", status_code=404)
    return user


def get_or_create_direct(user, other) -> Conversation:
    if user.pk == other.pk:
        raise ServiceError("You cannot message yourself.", code="self_message")
    ensure_not_blocked(user, other, "You can't message this account.")
    key = Conversation.make_direct_key(user.pk, other.pk)
    convo = Conversation.objects.filter(direct_key=key).first()
    if convo:
        return convo
    try:
        with transaction.atomic():
            convo = Conversation.objects.create(direct_key=key, created_by=user)
            ConversationParticipant.objects.bulk_create(
                [
                    ConversationParticipant(conversation=convo, user=user),
                    ConversationParticipant(conversation=convo, user=other),
                ]
            )
    except IntegrityError:
        convo = Conversation.objects.get(direct_key=key)
    return convo


@transaction.atomic
def create_group(user, participant_ids, title="") -> Conversation:
    ids = {int(i) for i in participant_ids} - {user.pk}
    if len(ids) < 2:
        raise ServiceError("A group needs at least two other people.", code="group_too_small")
    if len(ids) + 1 > MAX_GROUP_SIZE:
        raise ServiceError(f"Groups are limited to {MAX_GROUP_SIZE} people.", code="group_too_large")
    users = list(User.objects.filter(pk__in=ids, is_active=True).exclude(pk__in=hidden_user_ids(user)))
    if len(users) != len(ids):
        raise ServiceError("One or more people are unavailable.", code="user_unavailable")
    convo = Conversation.objects.create(is_group=True, title=(title or "").strip()[:80], created_by=user)
    ConversationParticipant.objects.bulk_create(
        [ConversationParticipant(conversation=convo, user=u) for u in [user, *users]]
    )
    return convo


def membership(user, conversation_id):
    m = (
        ConversationParticipant.objects.select_related("conversation")
        .filter(user=user, conversation_id=conversation_id)
        .first()
    )
    if m is None:
        raise ServiceError("Conversation not found.", code="not_found", status_code=404)
    return m


@transaction.atomic
def send_message(
    sender,
    conversation,
    *,
    content="",
    media_id=None,
    reply_to_id=None,
    shared_post=None,
    shared_reel=None,
    story=None,
    notification_type=NotificationType.MESSAGE,
):
    if not ConversationParticipant.objects.filter(conversation=conversation, user=sender).exists():
        raise ServiceError("Conversation not found.", code="not_found", status_code=404)
    if not conversation.is_group:
        other = (
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user=sender)
            .select_related("user")
        ).first()
        if other is not None and is_blocked_between(sender, other.user):
            raise ServiceError("You can't message this account.", code="blocked", status_code=403)
    content = (content or "").strip()
    if not (content or media_id or shared_post or shared_reel):
        raise ServiceError("Message cannot be empty.", code="empty_message")

    reply_to = None
    if reply_to_id:
        reply_to = Message.objects.filter(pk=reply_to_id, conversation=conversation).first()
        if reply_to is None:
            raise ServiceError("The message you are replying to was not found.", code="reply_not_found")

    asset = claim_one(sender, media_id, purposes={MediaPurpose.MESSAGE}) if media_id else None
    now = timezone.now()
    message = Message.objects.create(
        conversation=conversation,
        sender=sender,
        content=content,
        asset=asset,
        media_url=asset.secure_url if asset else "",
        media_public_id=asset.public_id if asset else "",
        media_type=asset.resource_type if asset else "",
        reply_to=reply_to,
        shared_post=shared_post,
        shared_reel=shared_reel,
        story=story,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at, updated_at=now)
    ConversationParticipant.objects.filter(conversation=conversation, user=sender).update(
        last_read_at=message.created_at
    )

    preview = content or (
        "Sent a photo" if message.media_type == "image" else "Sent a video" if message.media_type else "Shared a post"
    )
    for member in (
        ConversationParticipant.objects.filter(conversation=conversation, is_muted=False)
        .exclude(user=sender)
        .select_related("user")
    ):
        notify(
            recipient=member.user,
            sender=sender,
            notification_type=notification_type,
            target_type=TargetType.CONVERSATION,
            reference_id=conversation.pk,
            preview=preview,
        )
    return message


def share_content(sender, recipient_ids, *, text="", post=None, reel=None) -> list:
    """Send a post/reel to each recipient's direct thread. Returns conversation ids."""
    convo_ids = []
    for uid in dict.fromkeys(int(i) for i in recipient_ids):
        if uid == sender.pk:
            continue
        other = _active_user(uid)
        convo = get_or_create_direct(sender, other)
        send_message(
            sender,
            convo,
            content=text,
            shared_post=post,
            shared_reel=reel,
            notification_type=NotificationType.SHARE,
        )
        convo_ids.append(convo.pk)
    return convo_ids


@transaction.atomic
def mark_read(user, conversation_id):
    m = membership(user, conversation_id)
    now = timezone.now()
    m.last_read_at = now
    m.save(update_fields=["last_read_at"])
    updated = (
        Message.objects.filter(conversation_id=conversation_id, is_read=False, created_at__lte=now)
        .exclude(sender=user)
        .update(is_read=True)
    )
    Notification.objects.filter(
        recipient=user, target_type=TargetType.CONVERSATION, reference_id=str(conversation_id), is_read=False
    ).update(is_read=True)
    return updated


@transaction.atomic
def delete_message(user, message):
    if message.sender_id != user.pk:
        raise ServiceError("You can only delete your own messages.", code="permission_denied", status_code=403)
    if message.is_deleted:
        return message
    asset_id = message.asset_id
    message.is_deleted = True
    message.deleted_at = timezone.now()
    message.content = ""
    message.asset = None
    message.media_url = message.media_public_id = message.media_type = ""
    message.shared_post = message.shared_reel = None
    message.save()
    release_asset(asset_id)
    return message


def clear_conversation(user, conversation_id):
    m = membership(user, conversation_id)
    m.cleared_at = timezone.now()
    m.last_read_at = m.cleared_at
    m.save(update_fields=["cleared_at", "last_read_at"])


def set_muted(user, conversation_id, muted: bool):
    m = membership(user, conversation_id)
    m.is_muted = muted
    m.save(update_fields=["is_muted"])
    return m


def unread_conversations_count(user) -> int:
    from .selectors import conversations_for

    return conversations_for(user).filter(unread_count__gt=0).count()
