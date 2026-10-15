from django.contrib import admin

from .models import MpesaPayment


@admin.register(MpesaPayment)
class MpesaPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "purpose", "amount", "coins", "status", "receipt", "fulfilled", "created_at")
    list_filter = ("purpose", "status", "fulfilled", "created_at")
    search_fields = ("user__username", "receipt", "phone", "checkout_request_id")
    raw_id_fields = ("user",)
    list_select_related = ("user",)
    # The ledger of real money: read-only in the admin.
    readonly_fields = [f.name for f in MpesaPayment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
