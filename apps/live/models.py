import uuid

from django.conf import settings
from django.db import models


class LiveStream(models.Model):
    class Status(models.TextChoices):
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Everyone"
        FOLLOWERS = "followers", "Followers"
        PRIVATE = "private", "Chosen people"

    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_streams")
    title = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=40, blank=True)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    # Private lives: the only people (besides the host) who can find and watch.
    allowed_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    audio_only = models.BooleanField(default=False)
    allow_requests = models.BooleanField(default=True, help_text="Viewers may ask to join on screen.")
    paused_at = models.DateTimeField(null=True, blank=True)
    # Shown larger for everyone (a guest, or None for the host).
    pinned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    pinned_comment = models.ForeignKey(
        "LiveComment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reactions_count = models.PositiveIntegerField(default=0)
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
    class Kind(models.TextChoices):
        COMMENT = "comment", "Comment"
        QUESTION = "question", "Question for the host"

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    text = models.CharField(max_length=300)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.COMMENT)
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Removed by the host or a moderator (kept for the moderation log).
    is_removed = models.BooleanField(default=False)
    answered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=["stream", "id"])]


class LiveInvite(models.Model):
    """The host invites a viewer on screen as a guest."""

    class Status(models.TextChoices):
        PENDING = "pending", "Invited by the host"
        REQUESTED = "requested", "Asked to join"
        ACCEPTED = "accepted", "On screen"
        DECLINED = "declined", "Declined"
        ENDED = "ended", "Ended"

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="invites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # The host (or a moderator) turned this guest's microphone off.
    muted_by_host = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stream", "user"], name="liveinvite_unique")]


class LiveModerator(models.Model):
    """People the host trusts to manage comments and guests in one live."""

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="moderators")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stream", "user"], name="livemoderator_unique")]


class LiveBan(models.Model):
    """Removed from a live and kept out of it (no watching, commenting or rejoining)."""

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="bans")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    banned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stream", "user"], name="liveban_unique")]


class LivePoll(models.Model):
    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="polls")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    question = models.CharField(max_length=140)
    options = models.JSONField(default=list)  # ["Option A", "Option B", ...]
    is_open = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)


class LivePollVote(models.Model):
    poll = models.ForeignKey(LivePoll, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    option = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["poll", "user"], name="livepollvote_unique")]


class LiveModerationLog(models.Model):
    """Every host and moderator action in a live (mute, remove, ban, delete comment, ...)."""

    stream = models.ForeignKey(LiveStream, on_delete=models.CASCADE, related_name="moderation_log")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=24)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["stream", "-created_at"])]
