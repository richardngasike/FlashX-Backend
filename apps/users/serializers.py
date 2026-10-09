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
    }


def cover_payload(asset):
    if not asset:
        return None
    return {
        "url": cld.image_url(asset.public_id, 1500, 600, crop="fill", gravity="auto"),
        "thumbnail": cld.image_url(asset.public_id, 600, 240, crop="fill", gravity="auto"),
    }


class UserSummarySerializer(serializers.ModelSerializer):
    """Compact user shape embedded in posts, comments, messages, etc."""

    avatar = serializers.SerializerMethodField()
    is_online = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "full_name", "avatar", "is_verified", "is_online")
        read_only_fields = fields

    def get_avatar(self, obj) -> dict | None:
        return avatar_payload(obj.profile_image)


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


class UserProfileSerializer(UserListSerializer):
    cover = serializers.SerializerMethodField()
    is_me = serializers.SerializerMethodField()

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + (
            "website",
            "location",
            "cover",
            "following_count",
            "posts_count",
            "reels_count",
            "last_seen_at",
            "created_at",
            "is_me",
        )
        read_only_fields = fields

    def get_cover(self, obj) -> dict | None:
        return cover_payload(obj.cover_image)

    def get_is_me(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and request.user.pk == obj.pk)


class MeSerializer(UserProfileSerializer):
    class Meta(UserProfileSerializer.Meta):
        fields = UserProfileSerializer.Meta.fields + ("email", "date_joined")
        read_only_fields = fields


class UpdateMeSerializer(serializers.ModelSerializer):
    profile_image_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    cover_image_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = User
        fields = ("full_name", "username", "bio", "website", "location", "profile_image_id", "cover_image_id")
        extra_kwargs = {"username": {"validators": []}, "full_name": {"required": False}}

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


class DeleteAccountSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
