from django.conf import settings
from django.db import models
from django.db.models import Q


class Comment(models.Model):
    """Comment on a post or reel. ``parent`` makes it a reply (one level deep)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    post = models.ForeignKey("posts.Post", null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    reel = models.ForeignKey("reels.Reel", null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    parent_comment = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    content = models.TextField(max_length=1000)
    likes_count = models.PositiveIntegerField(default=0)
    replies_count = models.PositiveIntegerField(default=0)
    is_hidden = models.BooleanField(default=False, help_text="Hidden by moderation.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(post__isnull=False, reel__isnull=True) | Q(post__isnull=True, reel__isnull=False),
                name="comment_exactly_one_target",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "parent_comment", "created_at"]),
            models.Index(fields=["reel", "parent_comment", "created_at"]),
            models.Index(fields=["parent_comment", "created_at"]),
        ]

    def __str__(self):
        return f"Comment {self.pk}"

    @property
    def target(self):
        return self.post or self.reel
