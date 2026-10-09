from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from . import services
from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "target_type",
        "target_link",
        "reason",
        "reporter",
        "status",
        "reports_on_target",
        "created_at",
        "reviewed_by",
    )
    list_filter = ("status", "target_type", "reason", "created_at")
    search_fields = ("details", "reporter__username", "reported_user__username", "post__caption", "comment__content")
    raw_id_fields = ("reporter", "post", "reel", "comment", "reported_user", "reviewed_by")
    readonly_fields = ("reviewed_by", "reviewed_at", "created_at")
    date_hierarchy = "created_at"
    list_select_related = ("reporter", "reviewed_by")
    actions = ("dismiss", "mark_actioned", "hide_content", "hide_and_suspend")

    @admin.display(description="Target")
    def target_link(self, obj):
        target = obj.target
        if target is None:
            return "(deleted)"
        url = reverse(f"admin:{target._meta.app_label}_{target._meta.model_name}_change", args=[target.pk])
        return format_html('<a href="{}">{} #{}</a>', url, obj.target_type, target.pk)

    @admin.display(description="Open reports on target")
    def reports_on_target(self, obj):
        field = "reported_user" if obj.target_type == "user" else obj.target_type
        return Report.objects.filter(status="pending", **{f"{field}_id": getattr(obj, f"{field}_id")}).count()

    def _resolve(self, request, queryset, status, **kw):
        n = services.resolve(list(queryset.values_list("pk", flat=True)), request.user, status=status, **kw)
        self.message_user(request, f"Updated {n} reports.", messages.SUCCESS)

    @admin.action(description="Dismiss (no violation)")
    def dismiss(self, request, queryset):
        self._resolve(request, queryset, Report.Status.DISMISSED)

    @admin.action(description="Mark as actioned")
    def mark_actioned(self, request, queryset):
        self._resolve(request, queryset, Report.Status.ACTIONED)

    @admin.action(description="Hide reported content")
    def hide_content(self, request, queryset):
        self._resolve(request, queryset, Report.Status.ACTIONED, hide_content=True)

    @admin.action(description="Hide content and suspend its owner")
    def hide_and_suspend(self, request, queryset):
        self._resolve(request, queryset, Report.Status.ACTIONED, hide_content=True, deactivate_user=True)
