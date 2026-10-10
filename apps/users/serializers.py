from django.contrib.auth import get_user_model, password_validation
from django.db import transaction
from rest_framework import serializers

from apps.media import cloudinary_service as cld
from apps.media.models import MediaPurpose
from apps.media.services import claim_one, release_asset

from .validators import validate_username

User = get_user_model()


def avatar_payload(asset):
    if not asset:
        return None
    return {
        "url": cld.image_url(asset.public_id, 400, 400, crop="fill", gravity="face"),
        "thumbnail": cld.image_url(asset.public_id, 120, 120, crop="fill", gravity="face"),
        # Full size for the image viewer and downloads.
        "large": cld.image_url(asset.public_id, 1440, 1440),
        "original": cld.original_url(asset.public_id, "image"),
    }


def cover_payload(asset):
    if not asset:
        return None
    return {
        "url": cld.image_url(asset.public_id, 1500, 600, crop="fill", gravity="auto"),
        "thumbnail": cld.image_url(asset.public_id, 600, 240, crop="fill", gravity="auto"),
        "large": cld.image_url(asset.public_id, 2400),
        "original": cld.original_url(asset.public_id, "image"),
    }


def _viewer(context):
    request = context.get("request") if context else None
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


class UserSummarySerializer(serializers.ModelSerializer):
    """Compact user shape embedded in posts, comments, messages, etc."""

    avatar = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "full_name", "avatar", "is_verified", "is_online", "last_seen_at")
        read_only_fields = fields

    def get_avatar(self, obj) -> dict | None:
        return avatar_payload(obj.profile_image)

    def _activity_visible(self, obj) -> bool:
        return obj.activity_visible_to(_viewer(self.context))

    def get_is_online(self, obj) -> bool:
        return self._activity_visible(obj) and obj.is_online

    def get_last_seen_at(self, obj):
        """Hidden (null) when either person turned off activity status."""
        if not self._activity_visible(obj) or not obj.last_seen_at:
            return None
        return serializers.DateTimeField().to_representation(obj.last_seen_at)


class UserWithFollowStateSerializer(UserSummarySerializer):
    """Schema for an embedded user that also carries whether the viewer follows them."""

    is_following = serializers.BooleanField(read_only=True)

    class Meta(UserSummarySerializer.Meta):
        fields = UserSummarySerializer.Meta.fields + ("is_following",)
        read_only_fields = fields


class UserListSerializer(UserSummarySerializer):
    is_following = serializers.SerializerMethodField()
    follows_you = serializers.SerializerMethodField()

    class Meta(UserSummarySerializer.Meta):
        fields = UserSummarySerializer.Meta.fields + ("bio", "followers_count", "is_following", "follows_you")
        read_only_fields = fields

    def get_is_following(self, obj) -> bool:
        return bool(getattr(obj, "is_following", False))

    def get_follows_you(self, obj) -> bool:
        return bool(getattr(obj, "follows_you", False))


class SuggestedUserSerializer(UserListSerializer):
    """A People-you-may-know card. Needs ``suggestions`` (from suggestion_context) in the context."""

    mutual_count = serializers.SerializerMethodField()
    mutual_preview = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + ("mutual_count", "mutual_preview", "reason")
        read_only_fields = fields

    def _info(self, obj) -> dict:
        return self.context.get("suggestions", {}).get(obj.pk, {})

    def get_mutual_count(self, obj) -> int:
        return self._info(obj).get("mutual_count", 0)

    def get_mutual_preview(self, obj) -> list[str]:
        return self._info(obj).get("mutual_preview", [])

    def get_reason(self, obj) -> str:
        return self._info(obj).get("reason", "")


class UserProfileSerializer(UserListSerializer):
    cover = serializers.SerializerMethodField()
    is_me = serializers.SerializerMethodField()
    is_blocked = serializers.SerializerMethodField()
    follow_status = serializers.SerializerMethodField()
    can_view_content = serializers.SerializerMethodField()
    can_call = serializers.SerializerMethodField()

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + (
            "website",
            "location",
            "cover",
            "following_count",
            "posts_count",
            "reels_count",
            "created_at",
            "is_me",
            "is_blocked",
            "is_private",
            "follow_status",
            "can_view_content",
            "can_call",
        )
        read_only_fields = fields

    def get_follow_status(self, obj) -> str:
        """following | requested | none"""
        if getattr(obj, "is_following", False):
            return "following"
        return "requested" if getattr(obj, "is_requested", False) else "none"

    def get_can_view_content(self, obj) -> bool:
        viewer = _viewer(self.context)
        return (
            not obj.is_private
            or (viewer is not None and viewer.pk == obj.pk)
            or bool(getattr(obj, "is_following", False))
        )

    def get_can_call(self, obj) -> bool:
        """Calls are open between people who follow each other."""
        viewer = _viewer(self.context)
        if viewer is None or viewer.pk == obj.pk or not obj.allow_calls:
            return False
        return bool(getattr(obj, "is_following", False) and getattr(obj, "follows_you", False))

    def get_cover(self, obj) -> dict | None:
        return cover_payload(obj.cover_image)

    def get_is_blocked(self, obj) -> bool:
        return bool(getattr(obj, "is_blocked", False))

    def get_is_me(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.pk)


class MeSerializer(UserProfileSerializer):
    class Meta(UserProfileSerializer.Meta):
        fields = UserProfileSerializer.Meta.fields + (
            "email",
            "date_joined",
            "show_activity_status",
            "allow_calls",
            "notification_prefs",
        )
        read_only_fields = fields


class UpdateMeSerializer(serializers.ModelSerializer):
    profile_image_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    cover_image_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = User
        fields = (
            "full_name",
            "username",
            "bio",
            "website",
            "location",
            "profile_image_id",
            "cover_image_id",
            "show_activity_status",
            "is_private",
            "allow_calls",
            "notification_prefs",
        )
        extra_kwargs = {"username": {"validators": []}, "full_name": {"required": False}}

    NOTIFICATION_SWITCHES = (
        "pause_all",
        "likes",
        "comments",
        "mentions",
        "follows",
        "messages",
        "stories",
        "live",
        "calls",
    )

    def validate_notification_prefs(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object of on/off switches.")
        unknown = set(value) - set(self.NOTIFICATION_SWITCHES)
        if unknown:
            raise serializers.ValidationError(f"Unknown switches: {', '.join(sorted(unknown))}.")
        merged = dict(self.instance.notification_prefs or {}) if self.instance else {}
        merged.update({k: bool(v) for k, v in value.items()})
        return merged

    def validate_username(self, value):
        value = value.strip()
        validate_username(value)
        if User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This username is taken.")
        return value

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be empty.")
        return value

    @transaction.atomic
    def update(self, instance, validated_data):
        user = self.context["request"].user
        for field, purpose, attr in (
            ("profile_image_id", MediaPurpose.AVATAR, "profile_image"),
            ("cover_image_id", MediaPurpose.COVER, "cover_image"),
        ):
            if field not in validated_data:
                continue
            new_id = validated_data.pop(field)
            old_id = getattr(instance, f"{attr}_id")
            if new_id == old_id:
                continue
            new_asset = claim_one(user, new_id, purposes={purpose}) if new_id else None
            setattr(instance, attr, new_asset)
            instance.save(update_fields=[attr, "updated_at"])
            release_asset(old_id)
        return super().update(instance, validated_data)


class PresenceSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["online", "offline"])


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=80)
    username = serializers.CharField(max_length=30)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value

    def validate_username(self, value):
        value = value.strip()
        validate_username(value)
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is taken.")
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        candidate = User(username=attrs["username"], email=attrs["email"], full_name=attrs["full_name"])
        password_validation.validate_password(attrs["password"], user=candidate)
        return attrs

    def create(self, validated_data):
        validated_data.pop("confirm_password")
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            full_name=validated_data["full_name"],
            password=validated_data["password"],
        )


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(help_text="Username or email.")
    password = serializers.CharField(trim_whitespace=False)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        password_validation.validate_password(attrs["new_password"], user=user)
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(trim_whitespace=False)


class PasswordResetCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter the 6-digit code from the email."})
    new_password = serializers.CharField(trim_whitespace=False)


class DeleteAccountSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
