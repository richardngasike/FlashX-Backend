from django.conf import settings
from django.db import models


class Gif(models.Model):
    """
    A GIF in someone's library: uploaded as a GIF file, or made on FlashX
    from a video segment or a set of photos. The finished GIF is stored as
    an ordinary image on Cloudinary in the owner's folder; GIFs are silent.
    """

    class Source(models.TextChoices):
        UPLOAD = "upload", "Uploaded GIF"
        VIDEO = "video", "Made from a video"
        IMAGES = "images", "Made from photos"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gifs")
    title = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=8, choices=Source.choices)
    public_id = models.CharField(max_length=255, unique=True)
    url = models.URLField(max_length=500, db_index=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    bytes = models.PositiveIntegerField(default=0)
    frames = models.PositiveIntegerField(null=True, blank=True)
    # Public GIFs show up in other people's GIF search and can be reused; private ones only for the owner.
    is_public = models.BooleanField(default=False)
    uses_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["is_public", "-uses_count"]),
        ]

    def __str__(self):
        return self.title or f"GIF {self.pk}"
