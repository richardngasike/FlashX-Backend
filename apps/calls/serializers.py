from rest_framework import serializers

from apps.users.serializers import UserSummarySerializer

from .models import Call


class CallSerializer(serializers.ModelSerializer):
    caller = UserSummarySerializer(read_only=True)
    callee = UserSummarySerializer(read_only=True)
    is_outgoing = serializers.SerializerMethodField()
    duration = serializers.IntegerField(source="duration_seconds", read_only=True)

    class Meta:
        model = Call
        fields = (
            "id",
            "kind",
            "status",
            "caller",
            "callee",
            "is_outgoing",
            "created_at",
            "answered_at",
            "ended_at",
            "duration",
        )
        read_only_fields = fields

    def get_is_outgoing(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.caller_id == request.user.pk)


class StartCallSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=Call.Kind.choices, default=Call.Kind.VOICE)


class EndCallSerializer(serializers.Serializer):
    failed = serializers.BooleanField(required=False, default=False)
