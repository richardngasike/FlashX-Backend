from django.conf import settings
from django.db import models


class Block(models.Model):
    """``blocker`` has blocked ``blocked``. Either direction hides the two users from each other."""

    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="blocks_unique_pair"),
            models.CheckConstraint(condition=~models.Q(blocker=models.F("blocked")), name="blocks_no_self_block"),
        ]
        indexes = [models.Index(fields=["blocker", "-created_at"]), models.Index(fields=["blocked"])]

    def __str__(self):
        return f"{self.blocker_id} blocked {self.blocked_id}"
