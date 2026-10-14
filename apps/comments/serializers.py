from rest_framework import serializers

from apps.users.serializers import UserSummarySerializer

from .models import Comment


class CommentSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    parent_id = serializers.IntegerField(source="parent_comment_id", read_only=True)
    target_type = serializers.SerializerMethodField()
    target_id = serializers.SerializerMethodField()
    is_liked = serializers.BooleanField(read_only=True, default=False)
    can_delete = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_pin = serializers.SerializerMethodField()
    gif = serializers.SerializerMethodField()
    is_edited = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = (
            "id",
            "user",
            "content",
            "gif",
            "parent_id",
            "target_type",
            "target_id",
            "likes_count",
            "replies_count",
            "is_liked",
            "can_delete",
            "can_edit",
            "can_pin",
            "is_pinned",
            "is_edited",
            "created_at",
        )
        read_only_fields = fields

    def get_target_type(self, obj) -> str:
        return "post" if obj.post_id else "reel"

    def get_target_id(self, obj) -> int:
        return obj.post_id or obj.reel_id

    def get_gif(self, obj) -> dict | None:
        if not obj.gif_url:
            return None
        return {"url": obj.gif_url, "width": obj.gif_width, "height": obj.gif_height}

    def get_is_edited(self, obj) -> bool:
        return obj.edited_at is not None

    def _uid(self):
        request = self.context.get("request")
        return request.user.pk if request and request.user.is_authenticated else None

    def get_can_edit(self, obj) -> bool:
        return self._uid() == obj.user_id

    def get_can_pin(self, obj) -> bool:
        target = obj.post or obj.reel
        return obj.parent_comment_id is None and target is not None and self._uid() == target.author_id

    def get_can_delete(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        uid = request.user.pk
        target = obj.post or obj.reel
        return uid == obj.user_id or (target is not None and uid == target.author_id)


class CommentCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")
    parent_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    gif_url = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")
    gif_width = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=4096)
    gif_height = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=4096)

    def validate(self, attrs):
        attrs["content"] = (attrs.get("content") or "").strip()
        if not attrs["content"] and not attrs.get("gif_url"):
            raise serializers.ValidationError({"content": "Comment cannot be empty."})
        return attrs


class CommentUpdateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000, allow_blank=True)
