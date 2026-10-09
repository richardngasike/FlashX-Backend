from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def default_expiry():
    return timezone.now() + timedelta(hours=settings.FLASHX["STORY_LIFETIME_HOURS"])


class StoryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(expires_at__gt=timezone.now(), author__is_active=True)


class Story(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stories")
    asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="story"
    )
    cloudinary_url = models.URLField(max_length=500)
    cloudinary_public_id = models.CharField(max_length=255)
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    caption = models.CharField(max_length=200, blank=True)
    duration = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    views_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expiry, db_index=True)

    objects = StoryQuerySet.as_manager()

    class Meta:
        ordering = ("created_at", "id")
        verbose_name_plural = "stories"
        indexes = [models.Index(fields=["author", "expires_at"])]

    def __str__(self):
        return f"Story {self.pk} by {self.author_id}"

    @property
    def is_active(self):
        return self.expires_at > timezone.now()


class StoryView(models.Model):
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name="views")
    viewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=["story", "viewer"], name="storyview_unique")]
        indexes = [models.Index(fields=["viewer", "story"])]


class StoryReaction(models.Model):
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    reaction = models.CharField(max_length=16, help_text="Reaction key, e.g. 'fire', 'love', 'wow'.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["story", "user"], name="storyreaction_unique")]
