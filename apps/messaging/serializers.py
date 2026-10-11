from django.db.models import Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.users.serializers import UserSummarySerializer, avatar_payload

from .models import Message, MessageAttachment


def _post_preview(post, available):
    if post is None:
        return None
    if not available:
        return {"id": post.pk, "available": False}
    media = next(iter(post.media.all()), None)
    return {
        "id": post.pk,
        "available": True,
        "author": {"id": post.author_id, "username": post.author.username},
        "caption": post.caption[:140],
        "thumbnail": cld.variants(media.cloudinary_public_id, media.media_type)["thumbnail"] if media else None,
    }


def _reel_preview(reel):
    if reel is None:
        return None
    if reel.is_hidden:
        return {"id": reel.pk, "available": False}
    return {
        "id": reel.pk,
        "available": True,
        "author": {"id": reel.author_id, "username": reel.author.username},
        "caption": reel.caption[:140],
        "thumbnail": cld.video_poster_url(reel.cloudinary_public_id, width=360),
    }


def _story_preview(story):
    if story is None:
        return None
    if not story.is_active:
        return {"id": story.pk, "available": False}
    variants = cld.variants(story.cloudinary_public_id, story.media_type) or {}
    return {"id": story.pk, "available": True, "author_id": story.author_id, "thumbnail": variants.get("thumbnail")}


def attachment_payload(a) -> dict:
    """URLs for one attachment, by kind. Documents are served as the uploaded file."""
    data = {
        "id": a.pk,
        "kind": a.kind,
        "name": a.name,
        "format": a.format,
        "bytes": a.bytes,
        "duration": a.duration,
        "width": a.width,
        "height": a.height,
    }
    if a.kind in (MessageAttachment.Kind.IMAGE, MessageAttachment.Kind.VIDEO):
        data.update(cld.variants(a.public_id, a.resource_type) or {})
    elif a.kind in (MessageAttachment.Kind.AUDIO, MessageAttachment.Kind.VOICE):
        data["url"] = cld.original_url(a.public_id, "video")
        # MP3 rendition for players that can't decode the original container.
        data["mp3"] = cld.audio_url(a.public_id)
    else:
        data["url"] = cld.original_url(a.public_id, "raw")
    return data


def reactions_summary(reactions, me_id) -> list:
    """[{"emoji": "x", "count": 2, "mine": true}] most used first."""
    groups = {}
    for r in reactions:
        g = groups.setdefault(r.emoji, {"emoji": r.emoji, "count": 0, "mine": False, "first": r.created_at})
        g["count"] += 1
        g["mine"] = g["mine"] or r.user_id == me_id
        g["first"] = min(g["first"], r.created_at)
    rows = sorted(groups.values(), key=lambda g: (-g["count"], g["first"]))
    return [{"emoji": g["emoji"], "count": g["count"], "mine": g["mine"]} for g in rows]


class MessageSerializer(serializers.ModelSerializer):
    conversation_id = serializers.UUIDField(read_only=True)
    sender = UserSummarySerializer(read_only=True)
    content = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    shared_post = serializers.SerializerMethodField()
    shared_reel = serializers.SerializerMethodField()
    story = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    gif_url = serializers.SerializerMethodField()
    is_edited = serializers.SerializerMethodField()
    is_pinned = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = (
            "id",
            "conversation_id",
            "sender",
            "content",
            "media",
            "reply_to",
            "shared_post",
            "shared_reel",
            "story",
            "kind",
            "event",
            "is_read",
            "is_deleted",
            "is_mine",
            "status",
            "attachments",
            "reactions",
            "gif_url",
            "forwarded",
            "is_edited",
            "edited_at",
            "is_pinned",
            "pinned_at",
            "client_id",
            "created_at",
        )
        read_only_fields = fields

    def get_attachments(self, obj) -> list:
        if obj.is_deleted:
            return []
        return [attachment_payload(a) for a in obj.attachments.all()]

    def get_reactions(self, obj) -> list:
        if obj.is_deleted:
            return []
        return reactions_summary(obj.reactions.all(), self.context["request"].user.pk)

    def get_gif_url(self, obj) -> str | None:
        return None if obj.is_deleted or not obj.gif_url else obj.gif_url

    def get_is_edited(self, obj) -> bool:
        return obj.edited_at is not None and not obj.is_deleted

    def get_is_pinned(self, obj) -> bool:
        return obj.pinned_at is not None and not obj.is_deleted

    def get_content(self, obj) -> str | None:
        return None if obj.is_deleted else obj.content

    def get_event(self, obj) -> dict | None:
        """Group events only: what happened, ready to show as "Ama added Brian"."""
        if obj.kind != Message.Kind.SYSTEM:
            return None
        meta = obj.meta or {}
        return {"type": meta.get("event", ""), "user_ids": meta.get("user_ids", []), "title": meta.get("title", "")}

    def get_media(self, obj) -> dict | None:
        if obj.is_deleted or not obj.media_public_id:
            return None
        data = {"type": obj.media_type}
        data.update(cld.variants(obj.media_public_id, obj.media_type) or {"url": obj.media_url})
        return data

    def get_reply_to(self, obj) -> dict | None:
        r = obj.reply_to
        if r is None:
            return None
        return {
            "id": r.pk,
            "sender": {"id": r.sender_id, "username": r.sender.username},
            "content": None if r.is_deleted else r.content[:140],
            "media_type": None if r.is_deleted else (r.media_type or None),
            "attachment_kind": None if r.is_deleted else self._first_kind(r),
            "has_gif": bool(r.gif_url) and not r.is_deleted,
            "is_deleted": r.is_deleted,
        }

    @staticmethod
    def _first_kind(msg):
        first = next(iter(msg.attachments.all()), None)
        return first.kind if first else None

    def get_shared_post(self, obj) -> dict | None:
        visible = self.context.get("visible_post_ids", set())
        return _post_preview(obj.shared_post, obj.shared_post_id in visible)

    def get_shared_reel(self, obj) -> dict | None:
        return _reel_preview(obj.shared_reel)

    def get_story(self, obj) -> dict | None:
        return _story_preview(obj.story)

    def get_is_mine(self, obj) -> bool:
        return obj.sender_id == self.context["request"].user.pk

    def get_status(self, obj) -> str | None:
        if obj.kind == Message.Kind.SYSTEM or obj.sender_id != self.context["request"].user.pk:
            return None
        read_until = self.context.get("others_read_until")
        if obj.is_read or (read_until and read_until >= obj.created_at):
            return "read"
        delivered_until = self.context.get("others_delivered_until")
        return "delivered" if delivered_until and delivered_until >= obj.created_at else "sent"


class ConversationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    is_group = serializers.BooleanField()
    title = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    is_muted = serializers.BooleanField(default=False)
    is_pinned = serializers.SerializerMethodField()
    is_archived = serializers.SerializerMethodField()
    marked_unread = serializers.SerializerMethodField()
    invite_code = serializers.SerializerMethodField()
    is_blocked = serializers.SerializerMethodField()
    can_message = serializers.SerializerMethodField()
    admin_id = serializers.IntegerField(source="created_by_id", allow_null=True)
    is_admin = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    last_message_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()

    def _mine(self, obj):
        me = self.context["request"].user.pk
        return next((m for m in obj.memberships.all() if m.user_id == me), None)

    def get_unread_count(self, obj) -> int:
        count = getattr(obj, "unread_count", 0) or 0
        mine = self._mine(obj)
        # "Mark as unread" shows a badge even with nothing new.
        return max(count, 1) if mine is not None and mine.marked_unread else count

    def get_is_pinned(self, obj) -> bool:
        mine = self._mine(obj)
        return bool(mine and mine.pinned_at)

    def get_is_archived(self, obj) -> bool:
        mine = self._mine(obj)
        return bool(mine and mine.archived_at)

    def get_marked_unread(self, obj) -> bool:
        mine = self._mine(obj)
        return bool(mine and mine.marked_unread)

    def get_invite_code(self, obj) -> str | None:
        """Group admins only."""
        return obj.invite_code if self.get_is_admin(obj) else None

    def _others(self, obj):
        me = self.context["request"].user.pk
        return [m.user for m in obj.memberships.all() if m.user_id != me]

    def _block_sets(self):
        if "_blocked_by_me" not in self.context:
            from apps.blocks.models import Block

            me = self.context["request"].user
            rows = Block.objects.filter(Q(blocker=me) | Q(blocked=me)).values_list("blocker_id", "blocked_id")
            self.context["_blocked_by_me"] = {b for a, b in rows if a == me.pk}
            self.context["_blocking_me"] = {a for a, b in rows if b == me.pk}
        return self.context["_blocked_by_me"], self.context["_blocking_me"]

    def get_is_blocked(self, obj) -> bool:
        """Direct threads only: True when *you* blocked the other person. Never reveals that they blocked you."""
        if obj.is_group:
            return False
        blocked_by_me, _ = self._block_sets()
        return any(u.pk in blocked_by_me for u in self._others(obj))

    def get_can_message(self, obj) -> bool:
        """False for a direct thread with someone you blocked or who is unavailable to you."""
        if obj.is_group:
            return True
        blocked_by_me, blocking_me = self._block_sets()
        others = self._others(obj)
        return bool(others) and all(
            u.is_active and u.pk not in blocked_by_me and u.pk not in blocking_me for u in others
        )

    def get_is_admin(self, obj) -> bool:
        if not obj.is_group:
            return False
        mine = self._mine(obj)
        return obj.created_by_id == self.context["request"].user.pk or bool(mine and mine.is_admin)

    def get_member_count(self, obj) -> int:
        return len(obj.memberships.all())

    def get_title(self, obj) -> str:
        if obj.title:
            return obj.title
        others = self._others(obj)
        if obj.is_group:
            return ", ".join(u.full_name.split(" ")[0] or u.username for u in others[:3])
        return others[0].full_name or others[0].username if others else "FlashX user"

    def get_avatar(self, obj) -> dict | None:
        if obj.is_group:
            return avatar_payload(obj.image) if obj.image_id else None
        others = self._others(obj)
        return avatar_payload(others[0].profile_image) if others else None

    @extend_schema_field(UserSummarySerializer(many=True))
    def get_participants(self, obj):
        return UserSummarySerializer(self._others(obj), many=True, context=self.context).data

    def get_last_message(self, obj) -> dict | None:
        msg = self.context.get("last_messages", {}).get(getattr(obj, "last_message_id", None))
        if msg is None:
            return None
        if msg.kind == Message.Kind.SYSTEM:
            me = self.context["request"].user.pk
            actor = "You" if msg.sender_id == me else (msg.sender.full_name or msg.sender.username).split(" ")[0]
            text = f"{actor} {msg.content}"
        elif msg.is_deleted:
            text = "Message deleted"
        elif msg.content:
            text = msg.content[:120]
        else:
            text = _list_preview(msg)
        return {
            "id": msg.pk,
            "text": text,
            "sender_id": msg.sender_id,
            "is_mine": msg.sender_id == self.context["request"].user.pk,
            "kind": msg.kind,
            "created_at": msg.created_at,
        }


def _list_preview(msg) -> str:
    kinds = [a.kind for a in msg.attachments.all()]
    if msg.gif_url:
        return "GIF"
    if len(kinds) > 1:
        return f"{len(kinds)} attachments"
    if kinds:
        return {"image": "Photo", "video": "Video", "audio": "Voice message", "voice": "Voice message"}.get(
            kinds[0], "File"
        )
    if msg.media_type:
        return "Photo" if msg.media_type == "image" else "Video"
    if msg.shared_post_id:
        return "Shared a post"
    if msg.shared_reel_id:
        return "Shared a reel"
    return ""


class SendMessageSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField(required=False)
    recipient_id = serializers.IntegerField(required=False, min_value=1)
    content = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    media_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    media_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=10, allow_empty=True
    )
    # {"<asset id>": "Report.pdf"}: original file names for documents.
    attachment_names = serializers.DictField(
        child=serializers.CharField(max_length=200, allow_blank=True), required=False
    )
    gif_url = serializers.URLField(required=False, allow_blank=True, max_length=500)
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    # Set by the device; retrying with the same value never creates a second message.
    client_id = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate(self, attrs):
        if bool(attrs.get("conversation_id")) == bool(attrs.get("recipient_id")):
            raise serializers.ValidationError("Provide either conversation_id or recipient_id.")
        if (
            not (attrs.get("content") or "").strip()
            and not attrs.get("media_id")
            and not attrs.get("media_ids")
            and not attrs.get("gif_url")
        ):
            raise serializers.ValidationError("Message cannot be empty.")
        return attrs


class EditMessageSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000)


class ForwardSerializer(serializers.Serializer):
    conversation_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=20)
    recipient_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=20)

    def validate(self, attrs):
        if not attrs.get("conversation_ids") and not attrs.get("recipient_ids"):
            raise serializers.ValidationError("Choose who to forward to.")
        return attrs


class ReactSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=16)


class PinSerializer(serializers.Serializer):
    pinned = serializers.BooleanField(default=True)


class ListStateSerializer(serializers.Serializer):
    pinned = serializers.BooleanField(required=False)
    archived = serializers.BooleanField(required=False)
    unread = serializers.BooleanField(required=False)


class TypingSerializer(serializers.Serializer):
    typing = serializers.BooleanField()


class MemberRoleSerializer(serializers.Serializer):
    is_admin = serializers.BooleanField()


class BulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=100)
    scope = serializers.ChoiceField(choices=("me", "everyone"), default="me")


class StartConversationSerializer(serializers.Serializer):
    recipient_id = serializers.IntegerField(required=False, min_value=1)
    participant_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=255)
    title = serializers.CharField(required=False, allow_blank=True, max_length=80)

    def validate(self, attrs):
        if bool(attrs.get("recipient_id")) == bool(attrs.get("participant_ids")):
            raise serializers.ValidationError("Provide recipient_id for a direct chat or participant_ids for a group.")
        return attrs


class GroupUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=80)
    image_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    remove_image = serializers.BooleanField(required=False, default=False)


class MuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()


class GroupMembersSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=255)
