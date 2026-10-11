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
    # Groups: anyone with this code can join (admins create, reset or turn it off).
    invite_code = models.CharField(max_length=24, unique=True, null=True, blank=True)
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
    # Group admins besides the creator (who always is one).
    is_admin = models.BooleanField(default=False)
    # Per-member list state.
    pinned_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    marked_unread = models.BooleanField(default=False)
    # Messages up to here reached this member's device ("delivered").
    delivered_at = models.DateTimeField(null=True, blank=True)
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
    # A GIF or sticker from the GIF provider (URL on its CDN).
    gif_url = models.URLField(max_length=500, blank=True)
    forwarded = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Set by the sending device; a retry with the same id returns the first message.
    client_id = models.CharField(max_length=64, blank=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "client_id"],
                condition=~models.Q(client_id=""),
                name="message_sender_client_id_unique",
            )
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


class MessageAttachment(models.Model):
    """
    A file sent with a message: photo, video, audio (voice notes), GIF or a
    document. Forwarded messages copy the row (same Cloudinary file), so the
    asset link is kept only on the original for clean-up.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        VOICE = "voice", "Voice note"
        FILE = "file", "File"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    asset = models.ForeignKey("media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    public_id = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=8)
    format = models.CharField(max_length=16, blank=True)
    name = models.CharField(max_length=200, blank=True)
    bytes = models.PositiveBigIntegerField(default=0)
    duration = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")


class MessageReaction(models.Model):
    """One emoji per person per message; reacting again changes it."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="message_reaction_unique")]
        indexes = [models.Index(fields=["message", "emoji"])]
