from django.contrib import admin

from .models import RecentSearch


@admin.register(RecentSearch)
class RecentSearchAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "value", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "value")
    raw_id_fields = ("user",)
