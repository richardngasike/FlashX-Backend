from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.blocks.selectors import hidden_user_ids
from apps.blocks.services import ensure_not_blocked
from apps.core.exceptions import ServiceError
from apps.media.models import MediaPurpose
from apps.media.services import claim_one, release_asset
from apps.notifications import sync
from apps.notifications.models import Notification, NotificationType, TargetType
from apps.notifications.services import notify

from .models import Conversation, ConversationParticipant, Message, MessageHidden

User = get_user_model()
MAX_GROUP_SIZE = 32


def member_ids(conversation) -> list:
    return list(ConversationParticipant.objects.filter(conversation=conversation).values_list("user_id", flat=True))


def _sync(conversation, event, *, extra_ids=(), exclude=None, **data):
    ids = set(member_ids(conversation)) | set(extra_ids)
    if exclude is not None:
        ids.discard(exclude)
    sync.emit(ids, event, conversation_id=str(conversation.pk), collapse_key=f"{event}:{conversation.pk}", **data)


def _display(user) -> str:
    return (user.full_name or "").strip().split(" ")[0] or user.username


def system_event(conversation, actor, event, *, users=(), title=""):
    """
    Record a group event ("X created the group", "X added Y") as a message so
    it shows in the thread, moves the group to the top of everyone's list, and
    gives empty groups a last-message preview.
    """
    names = [_display(u) for u in users]
    texts = {
        "created": f'created the group "{conversation.title}"' if conversation.title else "created the group",
        "added": "added " + ", ".join(names),
        "removed": "removed " + ", ".join(names),
        "left": "left the group",
        "renamed": f'changed the group name to "{title}"',
        "photo": "changed the group photo",
        "photo_removed": "removed the group photo",
    }
    msg = Message.objects.create(
        conversation=conversation,
        sender=actor,
        kind=Message.Kind.SYSTEM,
        content=texts[event],
        meta={"event": event, "user_ids": [u.pk for u in users], "title": title},
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=msg.created_at, updated_at=msg.created_at)
    ConversationParticipant.objects.filter(conversation=conversation, user=actor).update(last_read_at=msg.created_at)
    return msg


def _active_user(user_id):
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ServiceError("This account is unavailable.", code="user_unavailable", status_code=404)
    return user


def get_or_create_direct(user, other) -> Conversation:
    if user.pk == other.pk:
        raise ServiceError("You cannot message yourself.", code="self_message")
    ensure_not_blocked(user, other, "Unblock this account to message it.")
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
    convo = Conversation.objects.create(
        is_group=True, title=(title or "").strip()[:80], created_by=user, last_message_at=timezone.now()
    )
    ConversationParticipant.objects.bulk_create(
        [ConversationParticipant(conversation=convo, user=u) for u in [user, *users]]
    )
    system_event(convo, user, "created")
    _notify_added(convo, user, users)
    _sync(convo, "conversation")
    return convo


def _notify_added(convo, actor, users):
    label = convo.title or "a group chat"
    for u in users:
        notify(
            recipient=u,
            sender=actor,
            notification_type=NotificationType.GROUP_ADD,
            target_type=TargetType.CONVERSATION,
            reference_id=convo.pk,
            preview=label,
        )


@transaction.atomic
def update_group(user, conversation_id, *, title=None, image_id=None, remove_image=False) -> Conversation:
    """Admin changes the group name or photo."""
    m = _group_membership(user, conversation_id)
    _require_admin(m)
    convo = m.conversation
    if title is not None:
        title = title.strip()[:80]
        if title != convo.title:
            convo.title = title
            convo.save(update_fields=["title", "updated_at"])
            system_event(convo, user, "renamed", title=title)
    if image_id or remove_image:
        old_id = convo.image_id
        new_asset = claim_one(user, image_id, purposes={MediaPurpose.GROUP}) if image_id else None
        if (new_asset.pk if new_asset else None) != old_id:
            convo.image = new_asset
            convo.save(update_fields=["image", "updated_at"])
            release_asset(old_id)
            system_event(convo, user, "photo" if new_asset else "photo_removed")
    _sync(convo, "conversation")
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
        if other is not None:
            ensure_not_blocked(sender, other.user, "Unblock this account to message it.")
            if not other.user.is_active:
                raise ServiceError("This account isn't available.", code="user_unavailable", status_code=404)
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
        kind=Message.Kind.USER,
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
    # Muted members get no notification, but their open screens still update.
    _sync(conversation, "message", exclude=sender.pk, message_id=message.pk)
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


def delete_message_for_me(user, message):
    """Hide a message from this user's view only. Anyone in the conversation can do this to any message."""
    if not ConversationParticipant.objects.filter(conversation_id=message.conversation_id, user=user).exists():
        raise ServiceError("Message not found.", code="not_found", status_code=404)
    MessageHidden.objects.get_or_create(message=message, user=user)
    return message


@transaction.atomic
def delete_message(user, message):
    """Delete for everyone: sender only. The bubble becomes "Message deleted" for all members."""
    if message.sender_id != user.pk or message.kind == Message.Kind.SYSTEM:
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
    _sync(message.conversation, "message_deleted", message_id=message.pk)
    return message


def clear_conversation(user, conversation_id):
    m = membership(user, conversation_id)
    m.cleared_at = timezone.now()
    m.last_read_at = m.cleared_at
    m.save(update_fields=["cleared_at", "last_read_at"])


def _group_membership(user, conversation_id):
    m = membership(user, conversation_id)
    if not m.conversation.is_group:
        raise ServiceError("This is not a group conversation.", code="not_a_group")
    return m


def _require_admin(m):
    if m.conversation.created_by_id != m.user_id:
        raise ServiceError("Only the group admin can do this.", code="permission_denied", status_code=403)


@transaction.atomic
def leave_group(user, conversation_id) -> bool:
    """Leave a group. The admin role passes to the longest-standing member; an empty group is deleted."""
    m = _group_membership(user, conversation_id)
    convo = m.conversation
    m.delete()
    remaining = ConversationParticipant.objects.filter(conversation=convo).order_by("joined_at", "id")
    if not remaining.exists():
        release_asset(convo.image_id)
        convo.delete()
        return True
    if convo.created_by_id == user.pk or convo.created_by_id is None:
        convo.created_by_id = remaining.first().user_id
        convo.save(update_fields=["created_by", "updated_at"])
    system_event(convo, user, "left")
    _sync(convo, "conversation", extra_ids=[user.pk])
    return True


@transaction.atomic
def add_group_members(user, conversation_id, user_ids) -> list:
    """Admin adds people. Returns the ids actually added (existing members and blocked users are skipped)."""
    m = _group_membership(user, conversation_id)
    _require_admin(m)
    convo = m.conversation
    current = set(ConversationParticipant.objects.filter(conversation=convo).values_list("user_id", flat=True))
    wanted = {int(i) for i in user_ids} - current - hidden_user_ids(user)
    if len(current) + len(wanted) > MAX_GROUP_SIZE:
        raise ServiceError(f"Groups are limited to {MAX_GROUP_SIZE} people.", code="group_too_large")
    users = list(User.objects.filter(pk__in=wanted, is_active=True))
    if not users:
        return []
    ConversationParticipant.objects.bulk_create(
        [ConversationParticipant(conversation=convo, user=u) for u in users],
        ignore_conflicts=True,
    )
    system_event(convo, user, "added", users=users)
    _notify_added(convo, user, users)
    _sync(convo, "conversation")
    return [u.pk for u in users]


@transaction.atomic
def remove_group_member(user, conversation_id, member_id) -> bool:
    m = _group_membership(user, conversation_id)
    if int(member_id) == user.pk:
        return leave_group(user, conversation_id)
    _require_admin(m)
    member = User.objects.filter(pk=member_id).first()
    deleted, _ = ConversationParticipant.objects.filter(conversation=m.conversation, user_id=member_id).delete()
    if deleted and member is not None:
        system_event(m.conversation, user, "removed", users=[member])
        _sync(m.conversation, "conversation", extra_ids=[member.pk])
    return bool(deleted)


def set_muted(user, conversation_id, muted: bool):
    m = membership(user, conversation_id)
    m.is_muted = muted
    m.save(update_fields=["is_muted"])
    return m


def unread_conversations_count(user) -> int:
    from .selectors import conversations_for

    return conversations_for(user).filter(unread_count__gt=0).count()
