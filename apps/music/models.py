from django.conf import settings
from django.db import models


class Sound(models.Model):
    """
    Audio that posts and reels can use. Either a licensed catalogue track
    (streamed from the provider, never copied) or an "original sound": the
    soundtrack of a FlashX video that other people can reuse.
    """

    class Source(models.TextChoices):
        JAMENDO = "jamendo", "Jamendo"
        FLASHX = "flashx", "Original sound"
        # Earlier builds attached 30-second store previews; kept for old posts, not offered any more.
        DEEZER = "deezer", "Deezer preview (legacy)"
        ITUNES = "itunes", "iTunes preview (legacy)"

    source = models.CharField(max_length=10, choices=Source.choices)
    external_id = models.CharField(max_length=64, blank=True, help_text="Track id at the provider.")
    title = models.CharField(max_length=200)
    artist = models.CharField(max_length=200, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    audio_url = models.URLField(max_length=500)
    duration = models.FloatField(null=True, blank=True)
    genre = models.CharField(max_length=60, blank=True)
    license_name = models.CharField(max_length=80, blank=True)
    license_url = models.URLField(max_length=300, blank=True)
    link_url = models.URLField(max_length=300, blank=True, help_text="Backlink to the track page (attribution).")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="original_sounds"
    )
    origin_post = models.OneToOneField(
        "posts.Post", null=True, blank=True, on_delete=models.CASCADE, related_name="original_sound"
    )
    origin_reel = models.OneToOneField(
        "reels.Reel", null=True, blank=True, on_delete=models.CASCADE, related_name="original_sound"
    )
    is_active = models.BooleanField(default=True, help_text="Inactive sounds stay on old posts but can't be picked.")
    uses_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-uses_count", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~models.Q(external_id=""),
                name="sound_source_external_unique",
            )
        ]
        indexes = [models.Index(fields=["source", "is_active", "-uses_count"]), models.Index(fields=["title"])]

    def __str__(self):
        return f"{self.title} - {self.artist}" if self.artist else self.title

    @property
    def key(self) -> str:
        """Public id used by the app: "<pk>" for stored sounds."""
        return str(self.pk)

    @property
    def label(self) -> str:
        return " - ".join(x for x in (self.title, self.artist) if x)[:120]
