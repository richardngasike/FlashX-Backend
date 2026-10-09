from django.contrib import admin

from .models import DeviceToken, Notification


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


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "app_version", "created_at", "last_seen_at")
    list_filter = ("platform",)
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("token", "created_at", "last_seen_at")
    list_select_related = ("user",)
