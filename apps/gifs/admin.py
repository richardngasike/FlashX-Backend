from django.contrib import admin

from .models import Gif


@admin.register(Gif)
class GifAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "owner", "source", "is_public", "uses_count", "bytes", "created_at")
    list_filter = ("source", "is_public")
    search_fields = ("title", "owner__username", "public_id")
    raw_id_fields = ("owner",)
    readonly_fields = ("public_id", "url", "width", "height", "bytes", "frames", "uses_count", "created_at")
