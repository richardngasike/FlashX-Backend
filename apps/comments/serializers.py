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

    class Meta:
        model = Comment
        fields = (
            "id",
            "user",
            "content",
            "parent_id",
            "target_type",
            "target_id",
            "likes_count",
            "replies_count",
            "is_liked",
            "can_delete",
            "created_at",
        )
        read_only_fields = fields

    def get_target_type(self, obj) -> str:
        return "post" if obj.post_id else "reel"

    def get_target_id(self, obj) -> int:
        return obj.post_id or obj.reel_id

    def get_can_delete(self, obj) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        uid = request.user.pk
        target = obj.post or obj.reel
        return uid == obj.user_id or (target is not None and uid == target.author_id)


class CommentCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000)
    parent_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Comment cannot be empty.")
        return value
