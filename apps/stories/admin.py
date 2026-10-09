from django.contrib import admin
from django.utils import timezone

from apps.core.admin_utils import preview, thumb

from .models import Story


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ("id", "media_thumb", "author", "media_type", "views_count", "active", "created_at", "expires_at")
    list_filter = ("media_type", "created_at")
    search_fields = ("author__username", "caption")
    raw_id_fields = ("author", "asset")
    readonly_fields = ("media_preview", "views_count", "created_at")
    date_hierarchy = "created_at"
    list_select_related = ("author",)
    actions = ("expire_now",)

    @admin.display(description="")
    def media_thumb(self, obj):
        return thumb(obj.cloudinary_public_id, obj.media_type, 40)

    @admin.display(description="Media")
    def media_preview(self, obj):
        return preview(obj.cloudinary_public_id, obj.media_type)

    @admin.display(boolean=True)
    def active(self, obj):
        return obj.is_active

    @admin.action(description="Expire selected stories now")
    def expire_now(self, request, queryset):
        self.message_user(request, f"Expired {queryset.update(expires_at=timezone.now())} stories.")
