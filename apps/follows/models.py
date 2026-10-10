from django.conf import settings
from django.db import models


class Follow(models.Model):
    follower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following_set")
    following = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follower_set")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["follower", "following"], name="follows_unique_pair"),
            models.CheckConstraint(condition=~models.Q(follower=models.F("following")), name="follows_no_self_follow"),
        ]
        indexes = [models.Index(fields=["following", "-created_at"]), models.Index(fields=["follower", "-created_at"])]

    def __str__(self):
        return f"{self.follower_id} -> {self.following_id}"


class FollowRequest(models.Model):
    """Pending follow of a private account. Approving turns it into a Follow."""

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follow_requests")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [models.UniqueConstraint(fields=["requester", "target"], name="follow_request_unique")]
        indexes = [models.Index(fields=["target", "-created_at"])]
