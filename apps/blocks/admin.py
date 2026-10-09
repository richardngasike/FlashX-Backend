from django.contrib import admin

from .models import Block


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    raw_id_fields = ("blocker", "blocked")
    date_hierarchy = "created_at"
    list_select_related = ("blocker", "blocked")
