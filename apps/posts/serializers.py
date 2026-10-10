from django.conf import settings
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.users.serializers import UserSummarySerializer, UserWithFollowStateSerializer

from .models import Category, Hashtag, Mood, Post, PostMedia, Visibility

User = get_user_model()


class CategorySerializer(serializers.ModelSerializer):
    cover = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "icon", "cover")

    def get_cover(self, obj) -> str | None:
        if obj.cover_public_id:
            return cld.image_url(obj.cover_public_id, 600, 600, crop="fill", gravity="auto")
        if obj.cover_url:
            return obj.cover_url
        # Fall back to the newest public image posted in the category.
        public_id = self.context.get("category_fallbacks", {}).get(obj.pk)
        return cld.image_url(public_id, 600, 600, crop="fill", gravity="auto") if public_id else None


def category_cover_fallbacks(categories):
    ids = [c.pk for c in categories if not c.cover_public_id and not c.cover_url]
    if not ids:
        return {}
    rows = (
        PostMedia.objects.filter(
            post__category_id__in=ids,
            post__visibility=Visibility.PUBLIC,
            post__is_hidden=False,
            post__author__is_active=True,
            media_type="image",
            order=0,
        )
        .order_by("post__category_id", "-post__created_at")
        .distinct("post__category_id")
        .values_list("post__category_id", "cloudinary_public_id")
    )
    return dict(rows)


class HashtagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hashtag
        fields = ("id", "name", "posts_count", "reels_count")


class PostMediaSerializer(serializers.ModelSerializer):
    urls = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = ("id", "media_type", "width", "height", "duration", "order", "urls")

    def get_urls(self, obj) -> dict:
        return cld.variants(obj.cloudinary_public_id, obj.media_type) or {"url": obj.cloudinary_url}


class PostSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    media = PostMediaSerializer(many=True, read_only=True)
    category = CategorySerializer(read_only=True)
    hashtags = serializers.SerializerMethodField()
    tagged_users = UserSummarySerializer(many=True, read_only=True)
    is_liked = serializers.BooleanField(read_only=True, default=False)
    is_saved = serializers.BooleanField(read_only=True, default=False)
    is_owner = serializers.SerializerMethodField()
    is_edited = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    sound = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id",
            "author",
            "type",
            "caption",
            "location",
            "mood",
            "music_title",
            "sound",
            "event",
            "visibility",
            "category",
            "hashtags",
            "tagged_users",
            "media",
            "likes_count",
            "comments_count",
            "saves_count",
            "shares_count",
            "comments_enabled",
            "is_liked",
            "is_saved",
            "is_owner",
            "is_edited",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(UserWithFollowStateSerializer)
    def get_author(self, obj):
        data = UserSummarySerializer(obj.author, context=self.context).data
        data["is_following"] = bool(getattr(obj, "author_is_following", False))
        return data

    def get_type(self, obj) -> str:
        types = {m.media_type for m in obj.media.all()}
        if not types:
            return "text"
        if len(types) > 1:
            return "mixed"
        kind = types.pop()
        return "carousel" if kind == "image" and len(obj.media.all()) > 1 else kind

    def get_hashtags(self, obj) -> list[str]:
        return [h.name for h in obj.hashtags.all()]

    def get_is_owner(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.author_id)

    def get_event(self, obj) -> dict | None:
        if not obj.event_title:
            return None
        return {"title": obj.event_title, "starts_at": obj.event_starts_at}

    def get_is_edited(self, obj) -> bool:
        return (obj.updated_at - obj.created_at).total_seconds() > 5

    def get_sound(self, obj) -> dict | None:
        return sound_block(obj, "origin_post")


class PostWriteSerializer(serializers.Serializer):
    caption = serializers.CharField(required=False, allow_blank=True, max_length=2200)
    location = serializers.CharField(required=False, allow_blank=True, max_length=120)
    visibility = serializers.ChoiceField(choices=Visibility.choices, required=False)
    category = serializers.SlugRelatedField(
        slug_field="slug", queryset=Category.objects.filter(is_active=True), required=False, allow_null=True
    )
    comments_enabled = serializers.BooleanField(required=False)
    mood = serializers.ChoiceField(choices=Mood.choices, required=False, allow_blank=True)
    music_title = serializers.CharField(required=False, allow_blank=True, max_length=120)
    # "123" (a FlashX sound) or "jamendo:456" (catalogue track); null removes the sound.
    sound_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=40)
    # Previous app version sent the whole sound object.
    sound = serializers.JSONField(required=False, allow_null=True, write_only=True)
    sound_start = serializers.FloatField(required=False, min_value=0, max_value=3600)
    sound_volume = serializers.FloatField(required=False, min_value=0, max_value=1)
    original_volume = serializers.FloatField(required=False, min_value=0, max_value=1)
    allow_sound_reuse = serializers.BooleanField(required=False)

    event_title = serializers.CharField(required=False, allow_blank=True, max_length=120)
    event_starts_at = serializers.DateTimeField(required=False, allow_null=True)
    media_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, allow_empty=True)
    tagged_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True, max_length=20
    )

    def validate_media_ids(self, value):
        limit = settings.FLASHX_MEDIA_LIMITS["POST_MAX_ITEMS"]
        if len(value) > limit:
            raise serializers.ValidationError(f"You can attach up to {limit} files.")
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate media.")
        return value

    def validate_tagged_user_ids(self, value):
        ids = list(dict.fromkeys(value))
        found = set(User.objects.filter(id__in=ids, is_active=True).values_list("id", flat=True))
        if len(found) != len(ids):
            raise serializers.ValidationError("One or more tagged users do not exist.")
        return ids

    def validate(self, attrs):
        if "sound" in attrs:
            legacy = attrs.pop("sound")
            if "sound_id" not in attrs:
                from apps.music.services import legacy_sound_id

                attrs["sound_id"] = legacy_sound_id(legacy)
        title, starts = attrs.get("event_title"), attrs.get("event_starts_at")
        if ("event_title" in attrs or "event_starts_at" in attrs) and bool((title or "").strip()) != bool(starts):
            raise serializers.ValidationError({"event_starts_at": "An event needs both a title and a start time."})
        if self.instance is None:
            has_extra = attrs.get("mood") or (attrs.get("event_title") or "").strip()
            if not (attrs.get("caption") or "").strip() and not attrs.get("media_ids") and not has_extra:
                raise serializers.ValidationError("A post needs text or at least one photo or video.")
        else:
            if "media_ids" in attrs:
                raise serializers.ValidationError({"media_ids": "Media cannot be changed after posting."})
            if "caption" in attrs and not attrs["caption"].strip() and not self.instance.media.exists():
                raise serializers.ValidationError({"caption": "A text post cannot be empty."})
        return attrs


class ShareSerializer(serializers.Serializer):
    recipient_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=20)
    message = serializers.CharField(required=False, allow_blank=True, max_length=1000)


def sound_block(obj, origin_attr):
    """The sound on a post or reel plus how to play it (start, volumes, whether it is the video's own audio)."""
    sound = obj.sound
    if sound is None:
        return None
    from apps.music.services import sound_payload

    data = sound_payload(sound)
    own_audio = getattr(sound, f"{origin_attr}_id", None) == obj.pk
    data.update(
        start=obj.sound_start,
        volume=obj.sound_volume,
        original_volume=obj.original_volume,
        # The video's own soundtrack: nothing extra to play.
        plays_separately=not own_audio,
    )
    return data
