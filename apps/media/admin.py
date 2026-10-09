from django.contrib import admin

from apps.core.admin_utils import preview, thumb

from .models import MediaAsset


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = (
        "thumbnail",
        "public_id",
        "owner",
        "purpose",
        "resource_type",
        "format",
        "size_mb",
        "is_attached",
        "is_managed",
        "created_at",
    )
    list_filter = ("purpose", "resource_type", "is_attached", "is_managed", "created_at")
    search_fields = ("public_id", "owner__username")
    raw_id_fields = ("owner",)
    readonly_fields = (
        "preview",
        "public_id",
        "version",
        "format",
        "secure_url",
        "bytes",
        "width",
        "height",
        "duration",
        "attached_at",
        "created_at",
    )
    date_hierarchy = "created_at"
    list_select_related = ("owner",)
    actions = ("delete_selected",)

    @admin.display(description="")
    def thumbnail(self, obj):
        return thumb(obj.public_id, obj.resource_type, 40)

    @admin.display(description="Preview")
    def preview(self, obj):
        return preview(obj.public_id, obj.resource_type)

    @admin.display(description="MB", ordering="bytes")
    def size_mb(self, obj):
        return round(obj.bytes / (1024 * 1024), 2)
