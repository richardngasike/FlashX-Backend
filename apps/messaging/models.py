import uuid

from django.conf import settings
from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    is_group = models.BooleanField(default=False)
    title = models.CharField(max_length=80, blank=True)
    # "<min_user_id>:<max_user_id>" for 1:1 chats, so each pair has one thread.
    direct_key = models.CharField(max_length=41, unique=True, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="ConversationParticipant", related_name="conversations"
    )
    image = models.ForeignKey("media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_message_at", "-created_at")

    def __str__(self):
        return self.title or str(self.id)

    @staticmethod
    def make_direct_key(a_id: int, b_id: int) -> str:
        lo, hi = sorted((int(a_id), int(b_id)))
        return f"{lo}:{hi}"


class ConversationParticipant(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_memberships"
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    # Hides the thread for this user until a new message arrives.
    cleared_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="participant_unique")]
        indexes = [models.Index(fields=["user", "conversation"])]


class Message(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    class Kind(models.TextChoices):
        USER = "user", "Message"
        SYSTEM = "system", "Group event"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    content = models.TextField(max_length=4000, blank=True)
    asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="message"
    )
    media_url = models.URLField(max_length=500, blank=True)
    media_public_id = models.CharField(max_length=255, blank=True)
    media_type = models.CharField(max_length=8, choices=MediaType.choices, blank=True)
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    shared_post = models.ForeignKey("posts.Post", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    shared_reel = models.ForeignKey("reels.Reel", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    story = models.ForeignKey(
        "stories.Story",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Set when the message is a reply to a story.",
    )
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.USER)
    # Group events: {"event": "created|added|removed|left|renamed|photo", "user_ids": [...], "title": "..."}
    meta = models.JSONField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["conversation", "-created_at"]),
            models.Index(fields=["conversation", "is_read"]),
        ]

    def __str__(self):
        return f"Message {self.pk}"


class MessageHidden(models.Model):
    """A message removed with Delete for Me. It stays visible to everyone else."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="hidden_for")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="message_hidden_unique")]
        indexes = [models.Index(fields=["user", "message"])]
