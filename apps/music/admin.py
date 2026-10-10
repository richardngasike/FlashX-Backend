from django.contrib import admin

from .models import Sound


@admin.register(Sound)
class SoundAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "source", "license_name", "uses_count", "is_active", "created_at")
    list_filter = ("source", "is_active")
    search_fields = ("title", "artist", "owner__username", "external_id")
    raw_id_fields = ("owner", "origin_post", "origin_reel")
    readonly_fields = ("uses_count", "created_at")
    actions = ["deactivate", "activate"]

    @admin.action(description="Stop offering selected sounds")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Offer selected sounds again")
    def activate(self, request, queryset):
        queryset.update(is_active=True)
