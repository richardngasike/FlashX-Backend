from django.conf import settings
from django.db import models


class GiftItem(models.Model):
    """A gift viewers can send during a live video, priced in coins."""

    class Category(models.TextChoices):
        GIFTS = "gifts", "Gifts"
        SPECIAL = "special", "Special"
        EVENTS = "events", "Events"

    class Size(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        LARGE = "large", "Large"
        EPIC = "epic", "Epic"

    slug = models.SlugField(max_length=30, unique=True, help_text="Also picks the artwork in the apps.")
    name = models.CharField(max_length=40)
    coins = models.PositiveIntegerField()
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.GIFTS)
    size = models.CharField(max_length=10, choices=Size.choices, default=Size.SMALL)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "coins", "id")

    def __str__(self):
        return f"{self.name} ({self.coins} coins)"


class Wallet(models.Model):
    """Coin balance. Every change goes through WalletTransaction (the ledger)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.BigIntegerField(default=0)
    welcome_bonus_given = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(balance__gte=0), name="wallet_balance_not_negative")]

    def __str__(self):
        return f"{self.user_id}: {self.balance}"


class GiftSend(models.Model):
    """One gift (possibly several of the same) sent during a live video."""

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gifts_sent")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gifts_received")
    gift = models.ForeignKey(GiftItem, on_delete=models.PROTECT, related_name="sends")
    quantity = models.PositiveIntegerField(default=1)
    coins = models.PositiveIntegerField(help_text="Total coins (price x quantity) at the time it was sent.")
    stream = models.ForeignKey(
        "live.LiveStream", null=True, blank=True, on_delete=models.SET_NULL, related_name="gifts"
    )
    # A retry with the same client_id returns the first send instead of charging twice.
    client_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "client_id"], condition=~models.Q(client_id=""), name="giftsend_client_unique"
            )
        ]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["sender", "-created_at"]),
            models.Index(fields=["stream", "-created_at"]),
        ]


class WalletTransaction(models.Model):
    """Ledger line: positive amounts add coins, negative ones spend them."""

    class Kind(models.TextChoices):
        WELCOME_BONUS = "welcome_bonus", "Welcome bonus"
        GIFT_SENT = "gift_sent", "Gift sent"
        GIFT_RECEIVED = "gift_received", "Gift received"
        RECHARGE = "recharge", "Recharge"
        TRANSFER_IN = "transfer_in", "Coins received"
        TRANSFER_OUT = "transfer_out", "Coins sent"
        ADJUSTMENT = "adjustment", "Adjustment"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet_transactions")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.BigIntegerField()
    balance_after = models.BigIntegerField()
    gift_send = models.ForeignKey(GiftSend, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    counterparty = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["user", "kind", "-created_at"])]
