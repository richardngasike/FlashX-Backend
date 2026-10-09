from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.admin_utils import URL_FIELD_OVERRIDES, preview, thumb

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    formfield_overrides = URL_FIELD_OVERRIDES
    list_display = (
        "avatar",
        "username",
        "full_name",
        "email",
        "is_verified",
        "is_active",
        "is_staff",
        "followers_count",
        "posts_count",
        "last_seen_at",
        "created_at",
    )
    list_display_links = ("avatar", "username")
    list_filter = ("is_verified", "is_active", "is_staff", "is_superuser", "created_at")
    search_fields = ("username", "full_name", "email")
    ordering = ("-created_at",)
    list_per_page = 50
    readonly_fields = (
        "avatar_preview",
        "cover_preview",
        "followers_count",
        "following_count",
        "posts_count",
        "reels_count",
        "last_seen_at",
        "last_login",
        "date_joined",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("profile_image", "cover_image")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Profile",
            {
                "fields": (
                    "full_name",
                    "email",
                    "bio",
                    "website",
                    "is_verified",
                    "profile_image",
                    "avatar_preview",
                    "cover_image",
                    "cover_preview",
                )
            },
        ),
        ("Stats", {"fields": ("followers_count", "following_count", "posts_count", "reels_count")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Activity", {"fields": ("last_seen_at", "last_login", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "full_name", "password1", "password2")}),
    )
    actions = ("verify", "unverify", "deactivate", "activate")

    @admin.display(description="")
    def avatar(self, obj):
        return thumb(obj.profile_image.public_id if obj.profile_image_id else None, size=36)

    @admin.display(description="Profile image")
    def avatar_preview(self, obj):
        return preview(obj.profile_image.public_id if obj.profile_image_id else None)

    @admin.display(description="Cover image")
    def cover_preview(self, obj):
        return preview(obj.cover_image.public_id if obj.cover_image_id else None)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("profile_image")

    @admin.action(description="Mark selected users as verified")
    def verify(self, request, queryset):
        self.message_user(request, f"Verified {queryset.update(is_verified=True)} users.")

    @admin.action(description="Remove verification")
    def unverify(self, request, queryset):
        self.message_user(request, f"Unverified {queryset.update(is_verified=False)} users.")

    @admin.action(description="Deactivate (suspend) selected users")
    def deactivate(self, request, queryset):
        n = queryset.filter(is_superuser=False).update(is_active=False)
        self.message_user(request, f"Suspended {n} users. Superusers were skipped.", messages.WARNING)

    @admin.action(description="Reactivate selected users")
    def activate(self, request, queryset):
        self.message_user(request, f"Reactivated {queryset.update(is_active=True)} users.")
