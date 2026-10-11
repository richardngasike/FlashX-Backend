from rest_framework import serializers

from apps.users.serializers import UserSummarySerializer

from .models import LiveComment, LiveStream


class LiveStreamSerializer(serializers.ModelSerializer):
    host = UserSummarySerializer(read_only=True)
    viewer_count = serializers.SerializerMethodField()
    is_host = serializers.SerializerMethodField()
    is_paused = serializers.SerializerMethodField()

    class Meta:
        model = LiveStream
        fields = (
            "id",
            "host",
            "title",
            "description",
            "category",
            "visibility",
            "audio_only",
            "allow_requests",
            "is_paused",
            "status",
            "started_at",
            "ended_at",
            "viewer_count",
            "peak_viewers",
            "total_viewers",
            "comments_count",
            "reactions_count",
            "is_host",
        )
        read_only_fields = fields

    def get_viewer_count(self, obj) -> int:
        count = getattr(obj, "viewer_count", None)
        if count is None:
            from .services import active_viewers

            count = active_viewers(obj).count()
        return count

    def get_is_paused(self, obj) -> bool:
        return obj.paused_at is not None

    def get_is_host(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.host_id)


class LiveStartSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    category = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    visibility = serializers.ChoiceField(choices=LiveStream.Visibility.choices, default=LiveStream.Visibility.PUBLIC)
    allowed_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=200, default=list
    )
    audio_only = serializers.BooleanField(default=False)
    allow_requests = serializers.BooleanField(default=True)
    notify_followers = serializers.BooleanField(default=True)


class LiveUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    category = serializers.CharField(max_length=40, required=False, allow_blank=True)
    allow_requests = serializers.BooleanField(required=False)


class LiveCommentSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    reply_to = serializers.SerializerMethodField()

    class Meta:
        model = LiveComment
        fields = ("id", "user", "text", "kind", "reply_to", "answered", "created_at")
        read_only_fields = fields

    def get_reply_to(self, obj) -> dict | None:
        r = obj.reply_to
        if r is None or r.is_removed:
            return None
        return {"id": r.pk, "username": r.user.username, "text": r.text[:100]}


class LiveCommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=300, trim_whitespace=True)
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    kind = serializers.ChoiceField(choices=LiveComment.Kind.choices, default=LiveComment.Kind.COMMENT)


class LiveUserSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class LiveToggleSerializer(serializers.Serializer):
    on = serializers.BooleanField()


class LivePinSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    comment_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class LivePollCreateSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=140)
    options = serializers.ListField(child=serializers.CharField(max_length=60), min_length=2, max_length=6)


class LiveVoteSerializer(serializers.Serializer):
    option = serializers.IntegerField(min_value=0, max_value=5)


class LiveReactionsSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=1, max_value=50)


class LiveReportSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=20)
    details = serializers.CharField(max_length=800, required=False, allow_blank=True, default="")


class LiveInviteSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class LiveInviteRespondSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
