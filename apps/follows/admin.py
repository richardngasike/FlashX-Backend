from django.contrib import admin

from .models import Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__username", "following__username")
    raw_id_fields = ("follower", "following")
    date_hierarchy = "created_at"
    list_select_related = ("follower", "following")
