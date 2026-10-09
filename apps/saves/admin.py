from django.contrib import admin

from .models import SavedItem


@admin.register(SavedItem)
class SavedItemAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "reel", "created_at")
    search_fields = ("user__username",)
    raw_id_fields = ("user", "post", "reel")
    list_select_related = ("user",)
