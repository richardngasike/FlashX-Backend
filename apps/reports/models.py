from django.conf import settings
from django.db import models
from django.db.models import Q


class Report(models.Model):
    class TargetType(models.TextChoices):
        POST = "post", "Post"
        REEL = "reel", "Reel"
        COMMENT = "comment", "Comment"
        USER = "user", "User"

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        HARASSMENT = "harassment", "Harassment or bullying"
        HATE = "hate", "Hate speech"
        VIOLENCE = "violence", "Violence or threats"
        NUDITY = "nudity", "Nudity or sexual content"
        MISINFORMATION = "misinformation", "False information"
        SCAM = "scam", "Scam or fraud"
        IMPERSONATION = "impersonation", "Impersonation"
        INTELLECTUAL_PROPERTY = "ip", "Intellectual property"
        SELF_HARM = "self_harm", "Self-harm"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACTIONED = "actioned", "Action taken"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_filed")
    target_type = models.CharField(max_length=10, choices=TargetType.choices, db_index=True)
    post = models.ForeignKey("posts.Post", null=True, blank=True, on_delete=models.CASCADE, related_name="reports")
    reel = models.ForeignKey("reels.Reel", null=True, blank=True, on_delete=models.CASCADE, related_name="reports")
    comment = models.ForeignKey(
        "comments.Comment", null=True, blank=True, on_delete=models.CASCADE, related_name="reports"
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="reports_received"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=1000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "post"],
                condition=Q(status="pending", post__isnull=False),
                name="report_unique_pending_post",
            ),
            models.UniqueConstraint(
                fields=["reporter", "reel"],
                condition=Q(status="pending", reel__isnull=False),
                name="report_unique_pending_reel",
            ),
            models.UniqueConstraint(
                fields=["reporter", "comment"],
                condition=Q(status="pending", comment__isnull=False),
                name="report_unique_pending_comment",
            ),
            models.UniqueConstraint(
                fields=["reporter", "reported_user", "target_type"],
                condition=Q(status="pending", target_type="user"),
                name="report_unique_pending_user",
            ),
        ]

    def __str__(self):
        return f"Report {self.pk}: {self.target_type} ({self.reason})"

    @property
    def target(self):
        return {"post": self.post, "reel": self.reel, "comment": self.comment, "user": self.reported_user}.get(
            self.target_type
        )
