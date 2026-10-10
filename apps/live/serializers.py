from rest_framework import serializers

from apps.users.serializers import UserSummarySerializer

from .models import LiveComment, LiveStream


class LiveStreamSerializer(serializers.ModelSerializer):
    host = UserSummarySerializer(read_only=True)
    viewer_count = serializers.SerializerMethodField()
    is_host = serializers.SerializerMethodField()

    class Meta:
        model = LiveStream
        fields = (
            "id",
            "host",
            "title",
            "status",
            "started_at",
            "ended_at",
            "viewer_count",
            "peak_viewers",
            "total_viewers",
            "comments_count",
            "is_host",
        )
        read_only_fields = fields

    def get_viewer_count(self, obj) -> int:
        count = getattr(obj, "viewer_count", None)
        if count is None:
            from .services import active_viewers

            count = active_viewers(obj).count()
        return count

    def get_is_host(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.host_id)


class LiveStartSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")


class LiveCommentSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = LiveComment
        fields = ("id", "user", "text", "created_at")
        read_only_fields = ("id", "user", "created_at")


class LiveCommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=300, trim_whitespace=True)


class LiveInviteSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class LiveInviteRespondSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
