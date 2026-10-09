from django.conf import settings
from django.db import models


class Reel(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reels")
    asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="reel"
    )
    video_url = models.URLField(max_length=500)
    cloudinary_public_id = models.CharField(max_length=255)
    caption = models.TextField(max_length=2200, blank=True)
    audio_title = models.CharField(max_length=120, blank=True, help_text="Soundtrack label shown on the reel.")
    duration = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    comments_enabled = models.BooleanField(default=True)
    hashtags = models.ManyToManyField("posts.Hashtag", through="ReelHashtag", related_name="reels", blank=True)

    views = models.PositiveIntegerField(default=0)
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    saves_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)

    is_hidden = models.BooleanField(default=False, help_text="Hidden by moderation.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["author", "-created_at"]),
            models.Index(fields=["is_hidden", "-created_at"]),
        ]

    def __str__(self):
        return f"Reel {self.pk} by {self.author_id}"


class ReelHashtag(models.Model):
    reel = models.ForeignKey(Reel, on_delete=models.CASCADE)
    hashtag = models.ForeignKey("posts.Hashtag", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["reel", "hashtag"], name="reelhashtag_unique")]
        indexes = [models.Index(fields=["hashtag", "-created_at"])]


class ReelView(models.Model):
    """One row per viewer per reel; ``Reel.views`` counts distinct viewers."""

    reel = models.ForeignKey(Reel, on_delete=models.CASCADE, related_name="view_records")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    watched_seconds = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["reel", "user"], name="reelview_unique")]
