from django.contrib import admin

from .models import GiftItem, GiftSend, Wallet, WalletTransaction


@admin.register(GiftItem)
class GiftItemAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "coins", "category", "size", "sort_order", "is_active")
    list_filter = ("category", "size", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "welcome_bonus_given", "updated_at")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("balance",)


@admin.register(GiftSend)
class GiftSendAdmin(admin.ModelAdmin):
    list_display = ("id", "sender", "recipient", "gift", "quantity", "coins", "stream", "created_at")
    raw_id_fields = ("sender", "recipient", "stream")
    list_select_related = ("sender", "recipient", "gift")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "kind", "amount", "balance_after", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__username",)
    raw_id_fields = ("user", "counterparty", "gift_send")

    def has_change_permission(self, request, obj=None):
        # The ledger is append-only.
        return False
