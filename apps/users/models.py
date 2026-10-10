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
    # False after the app reports it went to the background; True again on resume.
    presence_online = models.BooleanField(default=True)

    # Privacy
    show_activity_status = models.BooleanField(
        default=True, help_text="Others can see when this person is online or was last active."
    )
    is_private = models.BooleanField(default=False, help_text="New followers need approval; posts are followers-only.")
    allow_calls = models.BooleanField(default=True, help_text="People who follow each other can call.")
    notification_prefs = models.JSONField(default=dict, blank=True)
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
        if not self.last_seen_at or not self.presence_online:
            return False
        window = settings.FLASHX["ONLINE_WINDOW_SECONDS"]
        return timezone.now() - self.last_seen_at <= timedelta(seconds=window)

    def activity_visible_to(self, viewer) -> bool:
        """Online status and last seen are shared both ways or not at all."""
        if viewer is None or not getattr(viewer, "is_authenticated", False):
            return False
        if viewer.pk == self.pk:
            return True
        return self.show_activity_status and viewer.show_activity_status


class PasswordResetCode(models.Model):
    """A 6-digit code emailed for password reset. Only a keyed hash is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_codes")
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"reset code for {self.user_id}"
