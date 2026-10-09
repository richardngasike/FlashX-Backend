from django.conf import settings
from django.db import models


class Visibility(models.TextChoices):
    PUBLIC = "public", "Public"
    FOLLOWERS = "followers", "Followers"
    PRIVATE = "private", "Only me"


class Category(models.Model):
    """Discovery categories (Travel, Tech, Music, ...) managed in admin."""

    name = models.CharField(max_length=40, unique=True)
    slug = models.SlugField(max_length=40, unique=True)
    icon = models.CharField(max_length=40, blank=True, help_text="Client icon key, e.g. 'travel'.")
    cover_public_id = models.CharField(max_length=255, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "name")
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Hashtag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    posts_count = models.PositiveIntegerField(default=0)
    reels_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        indexes = [models.Index(fields=["name"], name="hashtag_name_prefix", opclasses=["varchar_pattern_ops"])]

    def __str__(self):
        return f"#{self.name}"


class Mood(models.TextChoices):
    HAPPY = "happy", "Happy"
    EXCITED = "excited", "Excited"
    GRATEFUL = "grateful", "Grateful"
    RELAXED = "relaxed", "Relaxed"
    LOVED = "loved", "Loved"
    MOTIVATED = "motivated", "Motivated"
    PROUD = "proud", "Proud"
    TIRED = "tired", "Tired"
    SAD = "sad", "Sad"


class Post(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    caption = models.TextField(max_length=2200, blank=True)
    location = models.CharField(max_length=120, blank=True)
    visibility = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.PUBLIC)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts")
    comments_enabled = models.BooleanField(default=True)
    mood = models.CharField(max_length=12, choices=Mood.choices, blank=True)
    music_title = models.CharField(max_length=120, blank=True, help_text="Song label shown on the post.")
    event_title = models.CharField(max_length=120, blank=True)
    event_starts_at = models.DateTimeField(null=True, blank=True)

    hashtags = models.ManyToManyField(Hashtag, through="PostHashtag", related_name="posts", blank=True)
    tagged_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="PostTag", related_name="tagged_in_posts", blank=True
    )

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
            models.Index(fields=["-created_at", "-id"]),
            models.Index(fields=["visibility", "is_hidden", "-created_at"]),
            models.Index(fields=["category", "-created_at"]),
        ]

    def __str__(self):
        return f"Post {self.pk} by {self.author_id}"


class PostMedia(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="media")
    asset = models.OneToOneField(
        "media.MediaAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="post_media"
    )
    cloudinary_url = models.URLField(max_length=500)
    cloudinary_public_id = models.CharField(max_length=255)
    media_type = models.CharField(max_length=8, choices=MediaType.choices)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")
        constraints = [models.UniqueConstraint(fields=["post", "order"], name="postmedia_unique_order")]

    def __str__(self):
        return self.cloudinary_public_id


class PostHashtag(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    hashtag = models.ForeignKey(Hashtag, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["post", "hashtag"], name="posthashtag_unique")]
        indexes = [models.Index(fields=["hashtag", "-created_at"]), models.Index(fields=["created_at"])]


class PostTag(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["post", "user"], name="posttag_unique")]
