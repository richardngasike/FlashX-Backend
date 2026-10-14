from rest_framework import serializers

from . import cloudinary_service as cld
from .models import MediaAsset, MediaPurpose, ResourceType


class SignUploadSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=MediaPurpose.choices)
    resource_type = serializers.ChoiceField(choices=ResourceType.choices)
    # Documents (resource_type "raw"): the original file name, for its extension.
    filename = serializers.CharField(required=False, allow_blank=True, max_length=200)


class RegisterUploadSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=MediaPurpose.choices)
    resource_type = serializers.ChoiceField(choices=ResourceType.choices)
    public_id = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=32)
    signature = serializers.CharField(max_length=128)
    # Optional client-reported metadata; only trusted when Admin API
    # verification is disabled.
    format = serializers.CharField(max_length=16, required=False, allow_blank=True)
    secure_url = serializers.URLField(required=False, allow_blank=True)
    bytes = serializers.IntegerField(required=False, min_value=0)
    width = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    height = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    duration = serializers.FloatField(required=False, min_value=0, allow_null=True)
    # Filter and text overlays chosen in the editor (videos); see services.clean_effects.
    effects = serializers.JSONField(required=False)


class DirectUploadSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=MediaPurpose.choices)
    file = serializers.FileField()


class MediaAssetSerializer(serializers.ModelSerializer):
    urls = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "purpose",
            "resource_type",
            "public_id",
            "format",
            "secure_url",
            "bytes",
            "width",
            "height",
            "duration",
            "effects",
            "is_attached",
            "urls",
            "created_at",
        )
        read_only_fields = fields

    def get_urls(self, obj):
        return cld.variants(obj.public_id, obj.resource_type)


def with_effects(urls: dict | None, asset) -> dict | None:
    """Add the asset's editor look (filter, text overlays) to a URL set."""
    if urls is None:
        return None
    effects = getattr(asset, "effects", None) if asset is not None else None
    if effects:
        urls = {**urls, "effects": effects}
    return urls


class MediaFieldsMixin:
    """Serializer helper for content rows that carry Cloudinary fields."""

    @staticmethod
    def media_payload(public_id, resource_type, url=None, width=None, height=None, duration=None):
        if not public_id and not url:
            return None
        data = {
            "type": resource_type,
            "width": width,
            "height": height,
            "duration": duration,
        }
        variants = cld.variants(public_id, resource_type) if public_id else None
        data.update(variants or {"url": url})
        return data
