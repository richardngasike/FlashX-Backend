from rest_framework import serializers

from .models import Report


class ReportCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=Report.TargetType.choices)
    target_id = serializers.IntegerField(min_value=1)
    reason = serializers.ChoiceField(choices=Report.Reason.choices)
    details = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class ReportSerializer(serializers.ModelSerializer):
    target_id = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = ("id", "target_type", "target_id", "reason", "details", "status", "created_at")
        read_only_fields = fields

    def get_target_id(self, obj):
        return obj.post_id or obj.reel_id or obj.comment_id or obj.reported_user_id


class ReasonSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()
