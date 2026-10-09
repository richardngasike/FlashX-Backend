from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.users.serializers import UserSummarySerializer, UserWithFollowStateSerializer

from .models import Reel


class ReelSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    video = serializers.SerializerMethodField()
    hashtags = serializers.SerializerMethodField()
    is_liked = serializers.BooleanField(read_only=True, default=False)
    is_saved = serializers.BooleanField(read_only=True, default=False)
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = Reel
        fields = (
            "id",
            "author",
            "video",
            "caption",
            "audio_title",
            "hashtags",
            "duration",
            "width",
            "height",
            "views",
            "likes_count",
            "comments_count",
            "saves_count",
            "shares_count",
            "comments_enabled",
            "is_liked",
            "is_saved",
            "is_owner",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(UserWithFollowStateSerializer)
    def get_author(self, obj):
        data = UserSummarySerializer(obj.author, context=self.context).data
        data["is_following"] = bool(getattr(obj, "author_is_following", False))
        return data

    def get_video(self, obj) -> dict:
        return cld.variants(obj.cloudinary_public_id, "video") or {"url": obj.video_url}

    def get_hashtags(self, obj) -> list[str]:
        return [h.name for h in obj.hashtags.all()]

    def get_is_owner(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.author_id)


class ReelCreateSerializer(serializers.Serializer):
    media_id = serializers.IntegerField(min_value=1)
    caption = serializers.CharField(required=False, allow_blank=True, max_length=2200)
    audio_title = serializers.CharField(required=False, allow_blank=True, max_length=120)
    comments_enabled = serializers.BooleanField(required=False, default=True)


class ReelUpdateSerializer(serializers.Serializer):
    caption = serializers.CharField(required=False, allow_blank=True, max_length=2200)
    audio_title = serializers.CharField(required=False, allow_blank=True, max_length=120)
    comments_enabled = serializers.BooleanField(required=False)


class ReelViewSerializer(serializers.Serializer):
    watched_seconds = serializers.FloatField(required=False, min_value=0, default=0)
