from django.contrib import admin

from apps.core.admin_utils import URL_FIELD_OVERRIDES, thumb

from .models import Category, Hashtag, Post, PostMedia


class PostMediaInline(admin.TabularInline):
    model = PostMedia
    extra = 0
    fields = ("order", "media_type", "media_preview", "cloudinary_public_id", "width", "height", "duration")
    readonly_fields = fields
    can_delete = False

    @admin.display(description="Preview")
    def media_preview(self, obj):
        return thumb(obj.cloudinary_public_id, obj.media_type, 80)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cover",
        "author",
        "short_caption",
        "visibility",
        "category",
        "likes_count",
        "comments_count",
        "is_hidden",
        "created_at",
    )
    list_display_links = ("id", "cover")
    list_filter = ("is_hidden", "visibility", "category", "created_at")
    search_fields = ("caption", "author__username", "location")
    raw_id_fields = ("author",)
    readonly_fields = ("likes_count", "comments_count", "saves_count", "shares_count", "created_at", "updated_at")
    date_hierarchy = "created_at"
    inlines = (PostMediaInline,)
    actions = ("hide", "unhide")
    list_select_related = ("author", "category")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("media")

    @admin.display(description="")
    def cover(self, obj):
        first = next(iter(obj.media.all()), None)
        return thumb(first.cloudinary_public_id, first.media_type, 40) if first else "Text"

    @admin.display(description="Caption")
    def short_caption(self, obj):
        return (obj.caption[:60] + "...") if len(obj.caption) > 60 else obj.caption

    @admin.action(description="Hide selected posts (moderation)")
    def hide(self, request, queryset):
        self.message_user(request, f"Hid {queryset.update(is_hidden=True)} posts.")

    @admin.action(description="Unhide selected posts")
    def unhide(self, request, queryset):
        self.message_user(request, f"Restored {queryset.update(is_hidden=False)} posts.")

    def delete_model(self, request, obj):
        from .services import delete_post

        delete_post(obj)

    def delete_queryset(self, request, queryset):
        from .services import delete_post

        for post in queryset:
            delete_post(post)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    formfield_overrides = URL_FIELD_OVERRIDES
    list_display = ("name", "slug", "icon", "order", "is_active", "cover_image")
    list_editable = ("order", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)

    @admin.display(description="Cover")
    def cover_image(self, obj):
        return thumb(obj.cover_public_id, "image", 40) if obj.cover_public_id else "-"


@admin.register(Hashtag)
class HashtagAdmin(admin.ModelAdmin):
    list_display = ("name", "posts_count", "reels_count", "created_at")
    search_fields = ("name",)
    ordering = ("-posts_count",)
    readonly_fields = ("posts_count", "reels_count", "created_at")
