from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.media.serializers import with_effects
from apps.users.serializers import UserSummarySerializer

from .models import Story

REACTIONS = ("fire", "love", "laugh", "wow", "sad", "clap")


class StorySerializer(serializers.ModelSerializer):
    author = UserSummarySerializer(read_only=True)
    media = serializers.SerializerMethodField()
    is_seen = serializers.BooleanField(read_only=True, default=False)
    my_reaction = serializers.CharField(read_only=True, default=None)
    is_owner = serializers.SerializerMethodField()
    views_count = serializers.SerializerMethodField()
    sound = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = (
            "id",
            "author",
            "media",
            "caption",
            "duration",
            "width",
            "height",
            "is_seen",
            "my_reaction",
            "is_owner",
            "views_count",
            "sound",
            "created_at",
            "expires_at",
        )
        read_only_fields = fields

    def get_media(self, obj) -> dict:
        data = {"type": obj.media_type}
        data.update(cld.variants(obj.cloudinary_public_id, obj.media_type) or {"url": obj.cloudinary_url})
        return with_effects(data, obj.asset if obj.asset_id else None)

    def get_sound(self, obj) -> dict | None:
        from apps.posts.serializers import sound_block

        return sound_block(obj, "origin_story")

    def get_is_owner(self, obj) -> bool:
        return obj.author_id == self.context["request"].user.pk

    def get_views_count(self, obj) -> int | None:
        # View counts are only shown to the story's author.
        return obj.views_count if obj.author_id == self.context["request"].user.pk else None


class StoryTraySerializer(serializers.Serializer):
    user = UserSummarySerializer()
    has_unseen = serializers.BooleanField()
    latest_at = serializers.DateTimeField()
    stories = StorySerializer(many=True)


class StoryCreateSerializer(serializers.Serializer):
    media_id = serializers.IntegerField(min_value=1)
    caption = serializers.CharField(required=False, allow_blank=True, max_length=200)
    sound_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=64)
    sound_start = serializers.FloatField(required=False, min_value=0, max_value=3600)
    sound_volume = serializers.FloatField(required=False, min_value=0, max_value=1)
    original_volume = serializers.FloatField(required=False, min_value=0, max_value=1)


class StoryReactSerializer(serializers.Serializer):
    reaction = serializers.ChoiceField(choices=[(r, r) for r in REACTIONS])


class StoryReplySerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000)

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Reply cannot be empty.")
        return value
