from rest_framework import serializers

from .models import Ad


class AdSerializer(serializers.ModelSerializer):
    call_to_action_label = serializers.CharField(source="get_call_to_action_display", read_only=True)

    class Meta:
        model = Ad
        fields = (
            "id",
            "advertiser_name",
            "advertiser_logo_url",
            "headline",
            "body",
            "image_url",
            "link_url",
            "call_to_action",
            "call_to_action_label",
        )
        read_only_fields = fields
