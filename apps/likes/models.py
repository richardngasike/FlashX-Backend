from django.conf import settings
from django.db import models
from django.db.models import Q

_ONE_TARGET = (
    Q(post__isnull=False, reel__isnull=True, comment__isnull=True)
    | Q(post__isnull=True, reel__isnull=False, comment__isnull=True)
    | Q(post__isnull=True, reel__isnull=True, comment__isnull=False)
)


class Like(models.Model):
    """A like on exactly one of: post, reel, comment."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="likes")
    post = models.ForeignKey("posts.Post", null=True, blank=True, on_delete=models.CASCADE, related_name="likes")
    reel = models.ForeignKey("reels.Reel", null=True, blank=True, on_delete=models.CASCADE, related_name="likes")
    comment = models.ForeignKey(
        "comments.Comment", null=True, blank=True, on_delete=models.CASCADE, related_name="likes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(condition=_ONE_TARGET, name="like_exactly_one_target"),
            models.UniqueConstraint(fields=["user", "post"], condition=Q(post__isnull=False), name="like_unique_post"),
            models.UniqueConstraint(fields=["user", "reel"], condition=Q(reel__isnull=False), name="like_unique_reel"),
            models.UniqueConstraint(
                fields=["user", "comment"], condition=Q(comment__isnull=False), name="like_unique_comment"
            ),
        ]
        indexes = [
            models.Index(fields=["post", "-created_at"]),
            models.Index(fields=["reel", "-created_at"]),
            models.Index(fields=["comment"]),
        ]

    @property
    def target(self):
        return self.post or self.reel or self.comment
