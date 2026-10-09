from rest_framework import serializers

from .models import RecentSearch


class RecentSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecentSearch
        fields = ("id", "kind", "value", "created_at")
        read_only_fields = ("id", "created_at")

    def validate_value(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Value cannot be empty.")
        return value
