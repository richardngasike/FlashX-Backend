from django.contrib import admin

from .models import Comment


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "short_content",
        "post",
        "reel",
        "parent_comment",
        "likes_count",
        "is_hidden",
        "created_at",
    )
    list_filter = ("is_hidden", "created_at")
    search_fields = ("content", "user__username")
    raw_id_fields = ("user", "post", "reel", "parent_comment")
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    actions = ("hide", "unhide")

    @admin.display(description="Content")
    def short_content(self, obj):
        return obj.content[:80]

    @admin.action(description="Hide selected comments")
    def hide(self, request, queryset):
        self.message_user(request, f"Hid {queryset.update(is_hidden=True)} comments.")

    @admin.action(description="Unhide selected comments")
    def unhide(self, request, queryset):
        self.message_user(request, f"Restored {queryset.update(is_hidden=False)} comments.")

    def delete_model(self, request, obj):
        from .services import delete_comment

        delete_comment(request.user, obj)

    def delete_queryset(self, request, queryset):
        from .services import delete_comment

        for c in queryset.select_related("post", "reel"):
            if type(c).objects.filter(pk=c.pk).exists():
                delete_comment(request.user, c)
