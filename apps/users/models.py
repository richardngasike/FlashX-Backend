from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .validators import validate_username


class FlashXUserManager(UserManager):
    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def active(self):
        return self.filter(is_active=True)


class User(AbstractUser):
    """
    FlashX account. Media fields hold Cloudinary references only.
    Counters are denormalised and maintained by the service layer.
    """

    first_name = None
    last_name = None

    username = models.CharField(max_length=30, unique=True, validators=[validate_username])
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=80)
    bio = models.CharField(max_length=300, blank=True)
    website = models.URLField(max_length=200, blank=True)
    location = models.CharField(max_length=80, blank=True)

    profile_image = models.ForeignKey(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cover_image = models.ForeignKey(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    is_verified = models.BooleanField(default=False, db_index=True)

    followers_count = models.PositiveIntegerField(default=0)
    following_count = models.PositiveIntegerField(default=0)
    posts_count = models.PositiveIntegerField(default=0)
    reels_count = models.PositiveIntegerField(default=0)

    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FlashXUserManager()

    REQUIRED_FIELDS = ["email", "full_name"]

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(Lower("username"), name="users_username_ci_unique"),
            models.UniqueConstraint(Lower("email"), name="users_email_ci_unique"),
        ]
        indexes = [models.Index(fields=["full_name"])]

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        if self.username:
            self.username = self.username.strip()
        super().save(*args, **kwargs)

    @property
    def is_online(self) -> bool:
        if not self.last_seen_at:
            return False
        window = settings.FLASHX["ONLINE_WINDOW_SECONDS"]
        return timezone.now() - self.last_seen_at <= timedelta(seconds=window)
