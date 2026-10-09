from django.conf import settings
from django.db import models


class NotificationType(models.TextChoices):
    LIKE = "like", "Like"
    COMMENT = "comment", "Comment"
    REPLY = "reply", "Comment reply"
    COMMENT_LIKE = "comment_like", "Comment like"
    FOLLOW = "follow", "Follow"
    MENTION = "mention", "Mention"
    TAG = "tag", "Tagged in post"
    STORY_REACTION = "story_reaction", "Story reaction"
    STORY_REPLY = "story_reply", "Story reply"
    MESSAGE = "message", "Message"
    SHARE = "share", "Shared content"


class TargetType(models.TextChoices):
    POST = "post", "Post"
    REEL = "reel", "Reel"
    COMMENT = "comment", "Comment"
    STORY = "story", "Story"
    USER = "user", "User"
    CONVERSATION = "conversation", "Conversation"


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_notifications", null=True
    )
    notification_type = models.CharField(max_length=24, choices=NotificationType.choices)
    target_type = models.CharField(max_length=16, choices=TargetType.choices)
    reference_id = models.CharField(max_length=64, help_text="Primary key of the target object.")
    preview = models.CharField(max_length=160, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["sender", "notification_type", "target_type", "reference_id"]),
        ]

    def __str__(self):
        return f"{self.notification_type} -> {self.recipient_id}"
