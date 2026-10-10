from django.contrib import admin

from .models import LiveComment, LiveStream


class LiveCommentInline(admin.TabularInline):
    model = LiveComment
    extra = 0
    fields = ("user", "text", "created_at")
    readonly_fields = fields
    raw_id_fields = ("user",)
    can_delete = True
    show_change_link = False


@admin.register(LiveStream)
class LiveStreamAdmin(admin.ModelAdmin):
    list_display = ("id", "host", "title", "status", "started_at", "ended_at", "peak_viewers", "comments_count")
    list_filter = ("status",)
    search_fields = ("host__username", "title")
    raw_id_fields = ("host",)
    readonly_fields = ("room_name", "started_at", "ended_at", "host_seen_at", "peak_viewers", "total_viewers")
    inlines = [LiveCommentInline]
    actions = ["end_streams"]

    @admin.action(description="End selected live videos")
    def end_streams(self, request, queryset):
        from . import services

        for stream in queryset.filter(status=LiveStream.Status.LIVE).select_related("host"):
            services.end(stream, stream.host)
