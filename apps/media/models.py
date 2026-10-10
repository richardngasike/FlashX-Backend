from django.conf import settings
from django.db import models


class MediaPurpose(models.TextChoices):
    AVATAR = "avatar", "Profile image"
    COVER = "cover", "Cover image"
    POST = "post", "Post media"
    STORY = "story", "Story media"
    REEL = "reel", "Reel video"
    MESSAGE = "message", "Message attachment"
    GROUP = "group", "Group photo"


class ResourceType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"


# Which resource types each purpose accepts.
PURPOSE_RESOURCE_TYPES = {
    MediaPurpose.AVATAR: {ResourceType.IMAGE},
    MediaPurpose.COVER: {ResourceType.IMAGE},
    MediaPurpose.POST: {ResourceType.IMAGE, ResourceType.VIDEO},
    MediaPurpose.STORY: {ResourceType.IMAGE, ResourceType.VIDEO},
    MediaPurpose.REEL: {ResourceType.VIDEO},
    MediaPurpose.MESSAGE: {ResourceType.IMAGE, ResourceType.VIDEO},
    MediaPurpose.GROUP: {ResourceType.IMAGE},
}


class MediaAsset(models.Model):
    """
    A file stored on Cloudinary. Only metadata lives in PostgreSQL.

    Assets are registered after upload, then *attached* exactly once to a
    piece of content (post, story, reel, message, profile). Unattached assets
    older than a day are purged by ``purge_orphan_media``.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media_assets")
    purpose = models.CharField(max_length=16, choices=MediaPurpose.choices, db_index=True)
    resource_type = models.CharField(max_length=8, choices=ResourceType.choices)
    public_id = models.CharField(max_length=255, unique=True)
    version = models.CharField(max_length=32, blank=True)
    format = models.CharField(max_length=16, blank=True)
    secure_url = models.URLField(max_length=500)
    bytes = models.PositiveBigIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True, help_text="Seconds, videos only.")
    is_attached = models.BooleanField(default=False, db_index=True)
    attached_at = models.DateTimeField(null=True, blank=True)
    # False for assets FlashX must never destroy (e.g. shared seed samples).
    is_managed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["owner", "is_attached", "created_at"])]

    def __str__(self):
        return self.public_id

    @property
    def is_video(self):
        return self.resource_type == ResourceType.VIDEO
