from django.db import models
from django.utils import timezone


class CallToAction(models.TextChoices):
    LEARN_MORE = "learn_more", "Learn more"
    SHOP_NOW = "shop_now", "Shop now"
    SIGN_UP = "sign_up", "Sign up"
    DOWNLOAD = "download", "Download"
    BOOK_NOW = "book_now", "Book now"
    CONTACT_US = "contact_us", "Contact us"
    ORDER_NOW = "order_now", "Order now"
    WATCH_MORE = "watch_more", "Watch more"


class AdQuerySet(models.QuerySet):
    def running(self):
        now = timezone.now()
        return self.filter(is_active=True).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
        )


class Ad(models.Model):
    """A sponsored post shown between feed posts, labelled Sponsored. Managed in the admin."""

    advertiser_name = models.CharField(max_length=80)
    advertiser_logo_url = models.URLField(max_length=500, blank=True)
    headline = models.CharField(max_length=90)
    body = models.TextField(max_length=500, blank=True)
    image_url = models.URLField(max_length=500, blank=True, help_text="Filled automatically when you upload an image.")
    image_public_id = models.CharField(max_length=255, blank=True, editable=False)
    link_url = models.URLField(max_length=500, help_text="Where the button sends people (https://...).")
    call_to_action = models.CharField(max_length=20, choices=CallToAction.choices, default=CallToAction.LEARN_MORE)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Leave empty to start now.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Leave empty to run until switched off.")
    weight = models.PositiveSmallIntegerField(default=1, help_text="Higher weight is shown more often (1-10).")
    impressions = models.PositiveIntegerField(default=0, editable=False)
    clicks = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AdQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["is_active", "starts_at", "ends_at"])]

    def __str__(self):
        return f"{self.advertiser_name}: {self.headline}"

    @property
    def click_through_rate(self) -> float:
        return round(self.clicks * 100 / self.impressions, 2) if self.impressions else 0.0
