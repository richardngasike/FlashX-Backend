import uuid

from django.conf import settings
from django.db import models


class LiveStream(models.Model):
    class Status(models.TextChoices):
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"

    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_streams")
    title = models.CharField(max_length=120, blank=True)
    room_name = models.CharField(max_length=64, unique=True, default="", editable=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.LIVE, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    host_seen_at = models.DateTimeField(null=True, blank=True)
    peak_viewers = models.PositiveIntegerField(default=0)
    total_viewers = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-started_at",)
        indexes = [models.Index(fields=["status", "-started_at"])]

    def save(self, *args, **kwargs):
        if not self.room_name:
            self.room_name = f"live-{uuid.uuid4().hex}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.host_id} live: {self.title or self.room_name}"


class LiveViewer(models.Model):
    """Presence: one row per person who opened the stream, refreshed while they watch."""

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="viewers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    joined_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stream", "user"], name="liveviewer_unique")]
        indexes = [models.Index(fields=["stream", "left_at", "last_seen_at"])]


class LiveComment(models.Model):
    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    text = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=["stream", "id"])]


class LiveInvite(models.Model):
    """The host invites a viewer on screen as a guest."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        ENDED = "ended", "Ended"

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="invites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stream", "user"], name="liveinvite_unique")]
