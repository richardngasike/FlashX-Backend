from django.conf import settings
from django.db import models

# Sources whose sounds can be added to new posts.
USABLE_SOURCES = ("jamendo", "openverse", "flashx")


class Sound(models.Model):
    """
    Audio that posts and reels can use. Either a licensed catalogue track
    (streamed from the provider, never copied) or an "original sound": the
    soundtrack of a FlashX video that other people can reuse.
    """

    class Source(models.TextChoices):
        JAMENDO = "jamendo", "Jamendo"
        OPENVERSE = "openverse", "Openverse (Creative Commons)"
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

    class Origin(models.TextChoices):
        CATALOGUE = "", "Catalogue track"
        VIDEO = "video", "Soundtrack of a video"
        UPLOAD = "upload", "Uploaded or recorded"

    origin = models.CharField(max_length=8, choices=Origin.choices, default=Origin.CATALOGUE, blank=True)
    description = models.CharField(max_length=300, blank=True)
    # Uploaded sounds: the audio file (deleted with the sound) and an optional cover image.
    asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="sound"
    )
    cover_asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="sound_cover"
    )
    # Private uploaded sounds are usable only by their owner.
    is_public = models.BooleanField(default=True)
    # Owner deleted it or moderators took it down: no longer played anywhere.
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_reason = models.CharField(max_length=120, blank=True)
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
        indexes = [
            models.Index(fields=["source", "is_active", "-uses_count"]),
            models.Index(fields=["title"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.title} - {self.artist}" if self.artist else self.title

    @property
    def key(self) -> str:
        """Public id used by the app: "<pk>" for stored sounds."""
        return str(self.pk)

    @property
    def label(self) -> str:
        return " - ".join(x for x in (self.title, self.artist) if x)[:120]


class SavedSound(models.Model):
    """A sound someone bookmarked in the picker or on its page."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_sounds")
    sound = models.ForeignKey(Sound, on_delete=models.CASCADE, related_name="saves")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [models.UniqueConstraint(fields=["user", "sound"], name="savedsound_unique")]
        indexes = [models.Index(fields=["user", "-created_at"])]
