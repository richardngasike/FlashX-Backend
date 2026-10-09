from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.users.serializers import UserSummarySerializer, avatar_payload

from .models import Message


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
            "is_read",
            "is_deleted",
            "is_mine",
            "status",
            "created_at",
        )
        read_only_fields = fields

    def get_content(self, obj) -> str | None:
        return None if obj.is_deleted else obj.content

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
            "is_deleted": r.is_deleted,
        }

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
        if obj.sender_id != self.context["request"].user.pk:
            return None
        read_until = self.context.get("others_read_until")
        return "read" if (obj.is_read or (read_until and read_until >= obj.created_at)) else "sent"


class ConversationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    is_group = serializers.BooleanField()
    title = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(default=0)
    is_muted = serializers.BooleanField(default=False)
    is_blocked = serializers.SerializerMethodField()
    admin_id = serializers.IntegerField(source="created_by_id", allow_null=True)
    is_admin = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    last_message_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()

    def _others(self, obj):
        me = self.context["request"].user.pk
        return [m.user for m in obj.memberships.all() if m.user_id != me]

    def get_is_blocked(self, obj) -> bool:
        """Direct threads only: True when either person has blocked the other (sending is disabled)."""
        if obj.is_group:
            return False
        if "_hidden_user_ids" not in self.context:
            from apps.blocks.selectors import hidden_user_ids

            self.context["_hidden_user_ids"] = hidden_user_ids(self.context["request"].user)
        hidden = self.context["_hidden_user_ids"]
        return any(u.pk in hidden for u in self._others(obj))

    def get_is_admin(self, obj) -> bool:
        return obj.is_group and obj.created_by_id == self.context["request"].user.pk

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
        others = self._others(obj)
        return avatar_payload(others[0].profile_image) if others else None

    @extend_schema_field(UserSummarySerializer(many=True))
    def get_participants(self, obj):
        return UserSummarySerializer(self._others(obj), many=True, context=self.context).data

    def get_last_message(self, obj) -> dict | None:
        msg = self.context.get("last_messages", {}).get(getattr(obj, "last_message_id", None))
        if msg is None:
            return None
        if msg.is_deleted:
            text = "Message deleted"
        elif msg.content:
            text = msg.content[:120]
        elif msg.media_type:
            text = "Photo" if msg.media_type == "image" else "Video"
        elif msg.shared_post_id:
            text = "Shared a post"
        elif msg.shared_reel_id:
            text = "Shared a reel"
        else:
            text = ""
        return {
            "id": msg.pk,
            "text": text,
            "sender_id": msg.sender_id,
            "is_mine": msg.sender_id == self.context["request"].user.pk,
            "created_at": msg.created_at,
        }


class SendMessageSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField(required=False)
    recipient_id = serializers.IntegerField(required=False, min_value=1)
    content = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    media_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, attrs):
        if bool(attrs.get("conversation_id")) == bool(attrs.get("recipient_id")):
            raise serializers.ValidationError("Provide either conversation_id or recipient_id.")
        if not (attrs.get("content") or "").strip() and not attrs.get("media_id"):
            raise serializers.ValidationError("Message cannot be empty.")
        return attrs


class StartConversationSerializer(serializers.Serializer):
    recipient_id = serializers.IntegerField(required=False, min_value=1)
    participant_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=31)
    title = serializers.CharField(required=False, allow_blank=True, max_length=80)

    def validate(self, attrs):
        if bool(attrs.get("recipient_id")) == bool(attrs.get("participant_ids")):
            raise serializers.ValidationError("Provide recipient_id for a direct chat or participant_ids for a group.")
        return attrs


class MuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()


class GroupMembersSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=31)
