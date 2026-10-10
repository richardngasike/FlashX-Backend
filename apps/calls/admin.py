from django.contrib import admin

from .models import Call


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ("created_at", "caller", "callee", "kind", "status", "answered_at", "ended_at")
    list_filter = ("kind", "status")
    search_fields = ("caller__username", "callee__username")
    raw_id_fields = ("caller", "callee", "ended_by")
    readonly_fields = ("room_name", "created_at", "answered_at", "ended_at")
    date_hierarchy = "created_at"
