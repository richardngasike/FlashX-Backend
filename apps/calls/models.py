import uuid

from django.conf import settings
from django.db import models


class Call(models.Model):
    class Kind(models.TextChoices):
        VOICE = "voice", "Voice"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        RINGING = "ringing", "Ringing"
        ACCEPTED = "accepted", "In progress"
        DECLINED = "declined", "Declined"
        MISSED = "missed", "Missed"
        CANCELLED = "cancelled", "Cancelled"
        ENDED = "ended", "Ended"
        BUSY = "busy", "Busy"
        FAILED = "failed", "Failed"

    ACTIVE = (Status.RINGING, Status.ACCEPTED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calls_made")
    callee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calls_received")
    kind = models.CharField(max_length=5, choices=Kind.choices, default=Kind.VOICE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RINGING, db_index=True)
    room_name = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["caller", "-created_at"]),
            models.Index(fields=["callee", "-created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.room_name:
            self.room_name = f"call-{uuid.uuid4().hex}"
        super().save(*args, **kwargs)

    @property
    def duration_seconds(self) -> int:
        if not self.answered_at:
            return 0
        end = self.ended_at
        if end is None:
            from django.utils import timezone

            end = timezone.now()
        return max(0, int((end - self.answered_at).total_seconds()))

    def other(self, user):
        return self.callee if user.pk == self.caller_id else self.caller

    def __str__(self):
        return f"{self.kind} call {self.caller_id} -> {self.callee_id} ({self.status})"
