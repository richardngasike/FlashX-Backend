from django.conf import settings
from django.db import models
from django.db.models import Q


class SavedItem(models.Model):
    """A saved post or reel (SavedPost in the spec, extended to reels)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_items")
    post = models.ForeignKey("posts.Post", null=True, blank=True, on_delete=models.CASCADE, related_name="saves")
    reel = models.ForeignKey("reels.Reel", null=True, blank=True, on_delete=models.CASCADE, related_name="saves")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(post__isnull=False, reel__isnull=True) | Q(post__isnull=True, reel__isnull=False),
                name="saved_exactly_one_target",
            ),
            models.UniqueConstraint(fields=["user", "post"], condition=Q(post__isnull=False), name="saved_unique_post"),
            models.UniqueConstraint(fields=["user", "reel"], condition=Q(reel__isnull=False), name="saved_unique_reel"),
        ]
        indexes = [models.Index(fields=["user", "-created_at"])]
