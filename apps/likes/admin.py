from django.contrib import admin

from .models import Like


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "reel", "comment", "created_at")
    search_fields = ("user__username",)
    raw_id_fields = ("user", "post", "reel", "comment")
    date_hierarchy = "created_at"
    list_select_related = ("user",)
