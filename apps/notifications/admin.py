from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "sender",
        "notification_type",
        "target_type",
        "reference_id",
        "is_read",
        "created_at",
    )
    list_filter = ("notification_type", "target_type", "is_read", "created_at")
    search_fields = ("recipient__username", "sender__username", "reference_id")
    raw_id_fields = ("recipient", "sender")
    date_hierarchy = "created_at"
    list_select_related = ("recipient", "sender")
