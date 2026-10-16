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

from .models import Conversation, ConversationParticipant, Message, MessageAttachment, MessageHidden, MessageReaction

User = get_user_model()
MAX_GROUP_SIZE = 256
MAX_ATTACHMENTS = 10
EDIT_WINDOW = timezone.timedelta(minutes=15)
TYPING_TTL = 6  # seconds a "typing" signal lasts without a refresh
# Only the GIF providers' own CDNs (media.giphy.com, media.tenor.com, ...).
GIF_HOSTS = ("giphy.com", "tenor.com")


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
        "joined": "joined using the group link",
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
    media_ids=None,
    gif_url="",
    reply_to_id=None,
    shared_post=None,
    shared_reel=None,
    story=None,
    client_id="",
    forwarded_from=None,
    notification_type=NotificationType.MESSAGE,
):
    client_id = (client_id or "").strip()[:64]
    if client_id:
        # A retry after a lost response: hand back the message already stored.
        existing = Message.objects.filter(sender=sender, client_id=client_id).first()
        if existing is not None:
            return existing
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
    gif_url = clean_gif_url(gif_url, sender)
    ids = [int(i) for i in (media_ids or [])]
    if media_id:
        ids.insert(0, int(media_id))
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_ATTACHMENTS:
        raise ServiceError(f"You can send up to {MAX_ATTACHMENTS} files at once.", code="too_many_attachments")
    has_forward = forwarded_from is not None and (
        forwarded_from.attachments.exists() or forwarded_from.media_public_id or forwarded_from.gif_url
    )
    if not (content or ids or gif_url or shared_post or shared_reel or has_forward):
        raise ServiceError("Message cannot be empty.", code="empty_message")

    reply_to = None
    if reply_to_id:
        reply_to = Message.objects.filter(pk=reply_to_id, conversation=conversation).first()
        if reply_to is None:
            raise ServiceError("The message you are replying to was not found.", code="reply_not_found")

    from apps.media.services import claim_assets

    assets = claim_assets(sender, ids, purposes={MediaPurpose.MESSAGE}, max_items=MAX_ATTACHMENTS) if ids else []
    # Older app versions read the single "media" field: mirror the first photo or video there.
    first_visual = next((a for a in assets if a.resource_type in ("image", "video") and not _is_audio(a)), None)
    now = timezone.now()
    try:
        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation,
                sender=sender,
                kind=Message.Kind.USER,
                content=content,
                gif_url=gif_url or (forwarded_from.gif_url if forwarded_from else ""),
                asset=first_visual,
                media_url=first_visual.secure_url if first_visual else "",
                media_public_id=first_visual.public_id if first_visual else "",
                media_type=first_visual.resource_type if first_visual else "",
                reply_to=reply_to,
                shared_post=shared_post,
                shared_reel=shared_reel,
                story=story,
                client_id=client_id,
                forwarded=forwarded_from is not None,
            )
    except IntegrityError:
        # The same client_id raced in from a parallel retry.
        return Message.objects.get(sender=sender, client_id=client_id)
    _attach(message, assets, forwarded_from)
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at, updated_at=now)
    ConversationParticipant.objects.filter(conversation=conversation, user=sender).update(
        last_read_at=message.created_at
    )

    preview = content or _attachment_preview(message)
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
    m.delivered_at = now
    m.marked_unread = False
    m.save(update_fields=["last_read_at", "delivered_at", "marked_unread"])
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
    own = list(message.attachments.values_list("asset_id", "public_id"))
    if message.asset_id:
        own.append((message.asset_id, message.media_public_id))
    public_ids = {p for _, p in own if p}
    # Forwarded copies point at the same Cloudinary files (with no asset link of their own).
    still_used = set(
        MessageAttachment.objects.filter(public_id__in=public_ids)
        .exclude(message=message)
        .values_list("public_id", flat=True)
    ) | set(
        Message.objects.filter(media_public_id__in=public_ids, is_deleted=False)
        .exclude(pk=message.pk)
        .values_list("media_public_id", flat=True)
    )
    asset_ids = {a for a, p in own if a and p not in still_used}
    message.is_deleted = True
    message.deleted_at = timezone.now()
    message.content = ""
    message.gif_url = ""
    message.asset = None
    message.media_url = message.media_public_id = message.media_type = ""
    message.shared_post = message.shared_reel = None
    message.pinned_at = None
    message.save()
    message.attachments.all().delete()
    message.reactions.all().delete()
    # Forwarded copies still use the file: keep it while any copy exists.
    for asset_id in asset_ids:
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


def is_admin(m) -> bool:
    return m.conversation.created_by_id == m.user_id or m.is_admin


def _require_admin(m):
    if not is_admin(m):
        raise ServiceError("Only group admins can do this.", code="permission_denied", status_code=403)


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


# ---------------------------------------------------------------------------
# Attachments and GIFs
# ---------------------------------------------------------------------------
def _is_audio(asset) -> bool:
    from apps.media import cloudinary_service as cld

    # Audio-only uploads have no picture size (Cloudinary may report an .m4a as "mp4").
    return asset.resource_type == "video" and (cld.is_audio_format(asset.format) or not asset.width)


def _attachment_kind(asset) -> str:
    if asset.resource_type == "raw":
        return MessageAttachment.Kind.FILE
    if _is_audio(asset):
        # Voice notes are recorded as AAC/M4A by the app and named voice-*.m4a.
        return MessageAttachment.Kind.AUDIO
    return asset.resource_type


def _attach(message, assets, forwarded_from=None):
    rows = [
        MessageAttachment(
            message=message,
            asset=a,
            kind=_attachment_kind(a),
            public_id=a.public_id,
            resource_type=a.resource_type,
            format=a.format,
            name=_original_name(a),
            bytes=a.bytes,
            duration=a.duration,
            width=a.width,
            height=a.height,
            position=i,
        )
        for i, a in enumerate(assets)
    ]
    if forwarded_from is not None:
        start = len(rows)
        source = list(forwarded_from.attachments.all())
        if not source and forwarded_from.media_public_id:
            # A message from before attachments existed.
            source = [
                MessageAttachment(
                    kind=forwarded_from.media_type,
                    public_id=forwarded_from.media_public_id,
                    resource_type=forwarded_from.media_type,
                )
            ]
        for i, a in enumerate(source):
            rows.append(
                MessageAttachment(
                    message=message,
                    asset=None,
                    kind=a.kind,
                    public_id=a.public_id,
                    resource_type=a.resource_type,
                    format=a.format,
                    name=a.name,
                    bytes=a.bytes,
                    duration=a.duration,
                    width=a.width,
                    height=a.height,
                    position=start + i,
                )
            )
        if source and not message.media_public_id and forwarded_from.media_public_id:
            Message.objects.filter(pk=message.pk).update(
                media_url=forwarded_from.media_url,
                media_public_id=forwarded_from.media_public_id,
                media_type=forwarded_from.media_type,
            )
    if rows:
        MessageAttachment.objects.bulk_create(rows)


def _original_name(asset) -> str:
    # Raw public ids end with the original extension; the readable name is sent by the app.
    tail = asset.public_id.rsplit("/", 1)[-1]
    return tail[:200]


def _attachment_preview(message) -> str:
    kinds = list(message.attachments.values_list("kind", flat=True))
    if message.gif_url:
        return "Sent a GIF"
    if not kinds:
        if message.media_type:
            return "Sent a photo" if message.media_type == "image" else "Sent a video"
        return "Shared a post" if message.shared_post_id else "Shared a reel" if message.shared_reel_id else ""
    if len(kinds) > 1:
        return f"Sent {len(kinds)} attachments"
    return {
        "image": "Sent a photo",
        "video": "Sent a video",
        "audio": "Sent a voice message",
        "voice": "Sent a voice message",
        "file": "Sent a file",
    }.get(kinds[0], "Sent an attachment")


def clean_gif_url(url, user=None) -> str:
    """
    GIFs can be sent by URL from the configured GIF providers' CDNs, or from
    the sender's FlashX GIF library (their own GIFs and other people's public ones).
    """
    url = (url or "").strip()
    if not url:
        return ""
    from urllib.parse import urlparse

    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in GIF_HOSTS):
        return url[:500]
    if user is not None:
        from apps.gifs.services import usable_gif

        if usable_gif(user, url) is not None:
            return url[:500]
    raise ServiceError("That GIF can't be sent.", code="invalid_gif")


def set_attachment_names(message, names):
    """The app sends the original file names (documents) alongside the asset ids."""
    if not names:
        return
    by_asset = {a.asset_id: a for a in message.attachments.all() if a.asset_id}
    for asset_id, name in names.items():
        att = by_asset.get(int(asset_id))
        if att is not None and name:
            att.name = str(name).strip()[:200]
            att.save(update_fields=["name"])


# ---------------------------------------------------------------------------
# Editing, forwarding, reactions, pins
# ---------------------------------------------------------------------------
def _message_for(user, message_id) -> Message:
    msg = (
        Message.objects.filter(pk=message_id, conversation__memberships__user=user)
        .select_related("conversation", "sender")
        .distinct()
        .first()
    )
    if msg is None or MessageHidden.objects.filter(message=msg, user=user).exists():
        raise ServiceError("Message not found.", code="not_found", status_code=404)
    return msg


@transaction.atomic
def edit_message(user, message_id, content) -> Message:
    msg = _message_for(user, message_id)
    if msg.sender_id != user.pk or msg.kind == Message.Kind.SYSTEM or msg.is_deleted:
        raise ServiceError("You can only edit your own messages.", code="permission_denied", status_code=403)
    if timezone.now() - msg.created_at > EDIT_WINDOW:
        raise ServiceError("Messages can only be edited for 15 minutes.", code="edit_window_closed")
    content = (content or "").strip()
    if not content:
        raise ServiceError("Message cannot be empty.", code="empty_message")
    if content != msg.content:
        msg.content = content[:4000]
        msg.edited_at = timezone.now()
        msg.save(update_fields=["content", "edited_at"])
        _sync(msg.conversation, "message_edited", exclude=user.pk, message_id=msg.pk)
    return msg


def forward_message(user, message_id, *, conversation_ids=(), recipient_ids=()) -> list:
    """Copy a message (text, GIF and files) into other chats. Returns the new messages."""
    source = _message_for(user, message_id)
    if source.is_deleted or source.kind == Message.Kind.SYSTEM:
        raise ServiceError("This message can't be forwarded.", code="cannot_forward")
    targets = []
    for cid in dict.fromkeys(str(c) for c in conversation_ids):
        targets.append(membership(user, cid).conversation)
    for uid in dict.fromkeys(int(u) for u in recipient_ids):
        if uid != user.pk:
            targets.append(get_or_create_direct(user, _active_user(uid)))
    if not targets:
        raise ServiceError("Choose who to forward to.", code="no_recipients")
    if len(targets) > 20:
        raise ServiceError("You can forward to up to 20 chats at once.", code="too_many_recipients")
    sent = []
    for convo in dict.fromkeys(targets):
        sent.append(
            send_message(
                user,
                convo,
                content=source.content,
                shared_post=source.shared_post,
                shared_reel=source.shared_reel,
                forwarded_from=source,
            )
        )
    return sent


EMOJI_MAX_LEN = 16


@transaction.atomic
def react(user, message_id, emoji) -> Message:
    msg = _message_for(user, message_id)
    emoji = (emoji or "").strip()
    if not emoji or len(emoji) > EMOJI_MAX_LEN or msg.is_deleted or msg.kind == Message.Kind.SYSTEM:
        raise ServiceError("You can't react to this message.", code="invalid_reaction")
    MessageReaction.objects.update_or_create(message=msg, user=user, defaults={"emoji": emoji})
    _sync(msg.conversation, "message_reaction", exclude=user.pk, message_id=msg.pk)
    return msg


@transaction.atomic
def unreact(user, message_id) -> Message:
    msg = _message_for(user, message_id)
    MessageReaction.objects.filter(message=msg, user=user).delete()
    _sync(msg.conversation, "message_reaction", exclude=user.pk, message_id=msg.pk)
    return msg


@transaction.atomic
def pin_message(user, message_id, pinned: bool) -> Message:
    """Pin to the top of the chat. In groups only admins pin; in direct chats either person."""
    msg = _message_for(user, message_id)
    m = membership(user, msg.conversation_id)
    if msg.conversation.is_group:
        _require_admin(m)
    if msg.is_deleted or msg.kind == Message.Kind.SYSTEM:
        raise ServiceError("This message can't be pinned.", code="cannot_pin")
    msg.pinned_at = timezone.now() if pinned else None
    msg.pinned_by = user if pinned else None
    msg.save(update_fields=["pinned_at", "pinned_by"])
    _sync(msg.conversation, "message_pinned", message_id=msg.pk)
    return msg


# ---------------------------------------------------------------------------
# Chat list state: pin, archive, unread
# ---------------------------------------------------------------------------
def set_list_state(user, conversation_id, *, pinned=None, archived=None, unread=None):
    m = membership(user, conversation_id)
    now = timezone.now()
    fields = []
    if pinned is not None:
        if pinned and m.pinned_at is None:
            if ConversationParticipant.objects.filter(user=user, pinned_at__isnull=False).count() >= 5:
                raise ServiceError("You can pin up to 5 chats.", code="pin_limit")
        m.pinned_at = (m.pinned_at or now) if pinned else None
        fields.append("pinned_at")
    if archived is not None:
        m.archived_at = (m.archived_at or now) if archived else None
        fields.append("archived_at")
    if unread is not None:
        m.marked_unread = bool(unread)
        fields.append("marked_unread")
    if fields:
        m.save(update_fields=fields)
    return m


# ---------------------------------------------------------------------------
# Delivery and typing
# ---------------------------------------------------------------------------
def mark_delivered(user, conversation_ids=None):
    """
    Messages up to now reached this user's device (called when the app fetches
    chats). Senders are told only when something new was actually delivered.
    """
    from django.db.models import F, Q

    qs = ConversationParticipant.objects.filter(user=user).filter(
        Q(delivered_at__isnull=True) | Q(delivered_at__lt=F("conversation__last_message_at"))
    )
    if conversation_ids is not None:
        qs = qs.filter(conversation_id__in=[str(c) for c in conversation_ids])
    convo_ids = list(qs.values_list("conversation_id", flat=True)[:100])
    if not convo_ids:
        return 0
    ConversationParticipant.objects.filter(user=user, conversation_id__in=convo_ids).update(delivered_at=timezone.now())
    for convo in Conversation.objects.filter(pk__in=convo_ids, is_group=False):
        _sync(convo, "delivered", exclude=user.pk)
    return len(convo_ids)


def _typing_key(conversation_id) -> str:
    return f"typing:{conversation_id}"


def set_typing(user, conversation_id, typing: bool):
    """Remember who is typing for a few seconds; the app refreshes the signal while typing."""
    import time

    from django.core.cache import cache

    m = membership(user, conversation_id)
    key = _typing_key(conversation_id)
    state = {int(k): v for k, v in (cache.get(key) or {}).items() if v > time.time()}
    was_typing = user.pk in state
    if typing:
        state[user.pk] = time.time() + TYPING_TTL
    else:
        state.pop(user.pk, None)
    cache.set(key, state, TYPING_TTL * 3)
    if typing != was_typing:
        # Only changes are pushed; refreshes ride on polling.
        _sync(m.conversation, "typing", exclude=user.pk, typing="1" if typing else "0")


def typing_users(user, conversation_id) -> list:
    import time

    from django.core.cache import cache

    membership(user, conversation_id)
    state = cache.get(_typing_key(conversation_id)) or {}
    ids = [int(k) for k, v in state.items() if v > time.time() and int(k) != user.pk]
    if not ids:
        return []
    hidden = hidden_user_ids(user)
    return list(User.objects.filter(pk__in=ids, is_active=True).exclude(pk__in=hidden))


# ---------------------------------------------------------------------------
# Group admins and invite links
# ---------------------------------------------------------------------------
@transaction.atomic
def set_admin(user, conversation_id, member_id, admin: bool):
    m = _group_membership(user, conversation_id)
    _require_admin(m)
    convo = m.conversation
    if int(member_id) == convo.created_by_id:
        raise ServiceError("The group creator is always an admin.", code="permission_denied", status_code=403)
    updated = ConversationParticipant.objects.filter(conversation=convo, user_id=member_id).update(is_admin=admin)
    if not updated:
        raise ServiceError("That person is not in this group.", code="not_found", status_code=404)
    _sync(convo, "conversation")


def invite_link(user, conversation_id, *, reset=False, disable=False):
    """Create, reset or turn off a group's invite code (admins only)."""
    import secrets

    m = _group_membership(user, conversation_id)
    _require_admin(m)
    convo = m.conversation
    if disable:
        convo.invite_code = None
    elif reset or not convo.invite_code:
        convo.invite_code = secrets.token_urlsafe(12)
    convo.save(update_fields=["invite_code", "updated_at"])
    return convo.invite_code


def group_for_code(code) -> Conversation:
    convo = Conversation.objects.filter(invite_code=code, is_group=True).first() if code else None
    if convo is None:
        raise ServiceError("This invite link is no longer valid.", code="invalid_invite", status_code=404)
    return convo


@transaction.atomic
def join_with_code(user, code) -> Conversation:
    convo = group_for_code(code)
    if ConversationParticipant.objects.filter(conversation=convo, user=user).exists():
        return convo
    admins = set(
        ConversationParticipant.objects.filter(conversation=convo, is_admin=True).values_list("user_id", flat=True)
    ) | {convo.created_by_id}
    if admins & hidden_user_ids(user):
        # Same answer as an invalid link: never reveal a block.
        raise ServiceError("This invite link is no longer valid.", code="invalid_invite", status_code=404)
    if ConversationParticipant.objects.filter(conversation=convo).count() >= MAX_GROUP_SIZE:
        raise ServiceError(f"Groups are limited to {MAX_GROUP_SIZE} people.", code="group_too_large")
    ConversationParticipant.objects.create(conversation=convo, user=user)
    system_event(convo, user, "joined")
    _sync(convo, "conversation")
    return convo


# ---------------------------------------------------------------------------
# GIF and sticker search (server-side, so API keys never reach the apps)
# ---------------------------------------------------------------------------
GIF_TIMEOUT = 6


def gif_search_enabled() -> bool:
    from django.conf import settings

    cfg = getattr(settings, "GIFS", {})
    return bool(cfg.get("GIPHY_API_KEY") or cfg.get("TENOR_API_KEY"))


def gif_search(query="", *, kind="gifs", offset=0, limit=24) -> dict:
    """
    Trending (empty query) or matching GIFs/stickers:
    {"enabled": bool, "provider": "giphy" | "tenor" | None,
     "results": [{"id", "url", "preview", "width", "height"}], "next_offset": int | None}

    The app shows the provider's attribution ("Powered by GIPHY" / Tenor),
    which both providers' API terms require.
    """
    import hashlib

    from django.conf import settings
    from django.core.cache import cache

    cfg = getattr(settings, "GIFS", {})
    if not gif_search_enabled():
        return {"enabled": False, "provider": None, "results": [], "next_offset": None}
    query = (query or "").strip()[:60]
    kind = "stickers" if kind == "stickers" else "gifs"
    offset = max(0, min(int(offset or 0), 500))
    limit = max(1, min(int(limit or 24), 50))
    key = "gifs:" + hashlib.sha1(f"{kind}|{query.lower()}|{offset}|{limit}".encode(), usedforsecurity=False).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        if cfg.get("GIPHY_API_KEY"):
            data = _giphy(cfg, query, kind, offset, limit)
        else:
            data = _tenor(cfg, query, kind, offset, limit)
    except Exception as exc:  # network errors, quota, bad keys
        import logging

        logging.getLogger(__name__).warning("GIF search failed: %s", exc)
        raise ServiceError("GIF search is unavailable right now.", code="gif_unavailable", status_code=503) from exc
    data["enabled"] = True
    data["provider"] = "giphy" if cfg.get("GIPHY_API_KEY") else "tenor"
    cache.set(key, data, 300)
    return data


def _giphy(cfg, query, kind, offset, limit) -> dict:
    import requests

    path = "search" if query else "trending"
    params = {"api_key": cfg["GIPHY_API_KEY"], "limit": limit, "offset": offset, "rating": cfg.get("RATING", "pg-13")}
    if query:
        params["q"] = query
    r = requests.get(f"https://api.giphy.com/v1/{kind}/{path}", params=params, timeout=GIF_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    results = []
    for item in body.get("data", []):
        images = item.get("images") or {}
        full = images.get("downsized") or images.get("original") or {}
        small = images.get("fixed_width") or images.get("fixed_width_downsampled") or full
        if not full.get("url"):
            continue
        results.append(
            {
                "id": str(item.get("id", "")),
                "url": full["url"].split("?")[0],
                "preview": (small.get("url") or full["url"]).split("?")[0],
                "width": int(full.get("width") or 0),
                "height": int(full.get("height") or 0),
            }
        )
    pagination = body.get("pagination") or {}
    total = int(pagination.get("total_count") or 0)
    nxt = offset + len(body.get("data", []))
    return {"results": results, "next_offset": nxt if results and nxt < min(total, 500) else None}


def _tenor(cfg, query, kind, offset, limit) -> dict:
    import requests

    path = "search" if query else "featured"
    params = {
        "key": cfg["TENOR_API_KEY"],
        "limit": limit,
        "media_filter": "gif,tinygif",
        "contentfilter": "medium",
        "client_key": "flashx",
    }
    if query:
        params["q"] = query
    if kind == "stickers":
        params["searchfilter"] = "sticker"
    if offset:
        params["pos"] = str(offset)
    r = requests.get(f"https://tenor.googleapis.com/v2/{path}", params=params, timeout=GIF_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    results = []
    for item in body.get("results", []):
        formats = item.get("media_formats") or {}
        full = formats.get("gif") or {}
        small = formats.get("tinygif") or full
        if not full.get("url"):
            continue
        dims = full.get("dims") or [0, 0]
        results.append(
            {
                "id": str(item.get("id", "")),
                "url": full["url"],
                "preview": small.get("url") or full["url"],
                "width": int(dims[0] or 0),
                "height": int(dims[1] or 0),
            }
        )
    nxt = body.get("next")
    try:
        nxt = int(nxt) if nxt else None
    except (TypeError, ValueError):
        nxt = None
    return {"results": results, "next_offset": nxt if results else None}
