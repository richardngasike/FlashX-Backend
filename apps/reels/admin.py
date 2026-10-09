from django.contrib import admin

from apps.core.admin_utils import preview, thumb

from .models import Reel


@admin.register(Reel)
class ReelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "poster",
        "author",
        "short_caption",
        "duration",
        "views",
        "likes_count",
        "is_hidden",
        "created_at",
    )
    list_display_links = ("id", "poster")
    list_filter = ("is_hidden", "created_at")
    search_fields = ("caption", "author__username")
    raw_id_fields = ("author", "asset")
    readonly_fields = (
        "video_preview",
        "views",
        "likes_count",
        "comments_count",
        "saves_count",
        "shares_count",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    list_select_related = ("author",)
    actions = ("hide", "unhide")

    @admin.display(description="")
    def poster(self, obj):
        return thumb(obj.cloudinary_public_id, "video", 40)

    @admin.display(description="Video")
    def video_preview(self, obj):
        return preview(obj.cloudinary_public_id, "video")

    @admin.display(description="Caption")
    def short_caption(self, obj):
        return obj.caption[:60]

    @admin.action(description="Hide selected reels")
    def hide(self, request, queryset):
        self.message_user(request, f"Hid {queryset.update(is_hidden=True)} reels.")

    @admin.action(description="Unhide selected reels")
    def unhide(self, request, queryset):
        self.message_user(request, f"Restored {queryset.update(is_hidden=False)} reels.")

    def delete_model(self, request, obj):
        from .services import delete_reel

        delete_reel(obj)

    def delete_queryset(self, request, queryset):
        from .services import delete_reel

        for reel in queryset:
            delete_reel(reel)
