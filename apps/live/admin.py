from django.contrib import admin

from .models import LiveBan, LiveComment, LiveModerationLog, LiveStream


class LiveCommentInline(admin.TabularInline):
    model = LiveComment
    extra = 0
    fields = ("user", "text", "kind", "is_removed", "created_at")
    readonly_fields = fields
    raw_id_fields = ("user",)
    can_delete = True
    show_change_link = False


class LiveModerationLogInline(admin.TabularInline):
    """What the host and moderators did in this live."""

    model = LiveModerationLog
    extra = 0
    fields = ("created_at", "actor", "action", "target_user", "detail")
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LiveBan)
class LiveBanAdmin(admin.ModelAdmin):
    list_display = ("stream", "user", "banned_by", "created_at")
    raw_id_fields = ("stream", "user", "banned_by")


@admin.register(LiveStream)
class LiveStreamAdmin(admin.ModelAdmin):
    list_display = ("id", "host", "title", "status", "started_at", "ended_at", "peak_viewers", "comments_count")
    list_filter = ("status", "visibility", "category")
    search_fields = ("host__username", "title")
    raw_id_fields = ("host", "pinned_user", "pinned_comment")
    filter_horizontal = ("allowed_users",)
    readonly_fields = ("room_name", "started_at", "ended_at", "host_seen_at", "peak_viewers", "total_viewers")
    inlines = [LiveCommentInline]

    def get_inlines(self, request, obj):
        return [LiveCommentInline, LiveModerationLogInline] if obj else []

    actions = ["end_streams"]

    @admin.action(description="End selected live videos")
    def end_streams(self, request, queryset):
        from . import services

        for stream in queryset.filter(status=LiveStream.Status.LIVE).select_related("host"):
            services.end(stream, stream.host)
