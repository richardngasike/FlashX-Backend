from django.conf import settings
from django.db import models


class MpesaPayment(models.Model):
    """One M-PESA STK push (Lipa na M-PESA Online) and what it pays for."""

    class Purpose(models.TextChoices):
        VERIFICATION = "verification", "Verified badge"
        COINS = "coins", "Coins"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for the customer"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled by the customer"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "No answer in time"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mpesa_payments")
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    amount = models.PositiveIntegerField(help_text="Kenya shillings.")
    coins = models.PositiveIntegerField(default=0, help_text="Coins added when paid (coin purchases).")
    phone = models.CharField(max_length=12, help_text="2547XXXXXXXX that received the prompt.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    merchant_request_id = models.CharField(max_length=64, blank=True)
    checkout_request_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)
    receipt = models.CharField(max_length=20, blank=True, db_index=True)
    fulfilled = models.BooleanField(default=False, help_text="The badge or coins were given.")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["user", "purpose", "status"])]

    def __str__(self):
        return f"{self.get_purpose_display()} KSh {self.amount} ({self.status})"
