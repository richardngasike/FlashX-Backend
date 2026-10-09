from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.users.serializers import UserSummarySerializer, UserWithFollowStateSerializer

from .models import Notification

MESSAGES = {
    "like": "liked your {target}.",
    "comment": "commented: {preview}",
    "reply": "replied to your comment: {preview}",
    "comment_like": "liked your comment.",
    "follow": "started following you.",
    "mention": "mentioned you: {preview}",
    "tag": "tagged you in a post.",
    "story_reaction": "reacted to your story.",
    "story_reply": "replied to your story: {preview}",
    "message": "sent you a message.",
    "share": "shared something with you.",
}


class NotificationSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="notification_type", read_only=True)
    sender = serializers.SerializerMethodField()
    text = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = (
            "id",
            "type",
            "sender",
            "text",
            "preview",
            "target_type",
            "reference_id",
            "target",
            "is_read",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(UserWithFollowStateSerializer(allow_null=True))
    def get_sender(self, obj):
        if obj.sender is None:
            return None
        data = UserSummarySerializer(obj.sender, context=self.context).data
        data["is_following"] = obj.sender_id in self.context.get("following_ids", set())
        return data

    def get_text(self, obj) -> str:
        template = MESSAGES.get(obj.notification_type, "")
        return template.format(target=obj.target_type, preview=obj.preview[:80])

    def get_target(self, obj) -> dict | None:
        """Thumbnail + route hint resolved in bulk by the view."""
        return self.context.get("targets", {}).get((obj.target_type, obj.reference_id))


class DeviceTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512, trim_whitespace=True)
    platform = serializers.ChoiceField(choices=["android", "ios"])
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")


class DeviceTokenDeleteSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512, trim_whitespace=True)
