from django.conf import settings
from django.db import models


class RecentSearch(models.Model):
    """Per-user recent search entries shown under the search bar."""

    class Kind(models.TextChoices):
        QUERY = "query", "Query"
        USER = "user", "User"
        HASHTAG = "hashtag", "Hashtag"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recent_searches")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=["user", "kind", "value"], name="recentsearch_unique")]
