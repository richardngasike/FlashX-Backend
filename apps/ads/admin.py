import logging

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from apps.core.admin_utils import URL_FIELD_OVERRIDES
from apps.media import cloudinary_service as cld

from .models import Ad

logger = logging.getLogger(__name__)


class AdForm(forms.ModelForm):
    image_file = forms.ImageField(
        required=False,
        help_text="Upload the ad image (JPG/PNG, square or 4:5 looks best). Stored on Cloudinary.",
    )
    logo_file = forms.ImageField(required=False, help_text="Optional advertiser logo (square).")

    class Meta:
        model = Ad
        fields = "__all__"


def _upload(file, folder):
    cld.require_configured()
    import cloudinary.uploader

    res = cloudinary.uploader.upload(file, folder=folder, resource_type="image", overwrite=False)
    return res["secure_url"], res["public_id"]


@admin.register(Ad)
class AdAdmin(admin.ModelAdmin):
    form = AdForm
    formfield_overrides = URL_FIELD_OVERRIDES
    list_display = (
        "preview",
        "headline",
        "advertiser_name",
        "is_active",
        "starts_at",
        "ends_at",
        "weight",
        "impressions",
        "clicks",
        "ctr",
    )
    list_display_links = ("preview", "headline")
    list_editable = ("is_active", "weight")
    list_filter = ("is_active", "call_to_action", "created_at")
    search_fields = ("headline", "advertiser_name", "body")
    readonly_fields = ("impressions", "clicks", "ctr", "large_preview", "created_at", "updated_at")
    fieldsets = (
        ("Advertiser", {"fields": ("advertiser_name", "logo_file", "advertiser_logo_url")}),
        ("Creative", {"fields": ("headline", "body", "image_file", "image_url", "large_preview")}),
        ("Action", {"fields": ("link_url", "call_to_action")}),
        ("Schedule", {"fields": ("is_active", "starts_at", "ends_at", "weight")}),
        ("Performance", {"fields": ("impressions", "clicks", "ctr", "created_at", "updated_at")}),
    )
    actions = ("activate", "deactivate")

    @admin.display(description="Image")
    def preview(self, obj):
        if not obj.image_url:
            return "-"
        return format_html(
            '<img src="{}" style="width:56px;height:56px;object-fit:cover;border-radius:6px">', obj.image_url
        )

    @admin.display(description="Preview")
    def large_preview(self, obj):
        if not obj.image_url:
            return "-"
        return format_html('<img src="{}" style="max-width:320px;border-radius:8px">', obj.image_url)

    @admin.display(description="CTR %")
    def ctr(self, obj):
        return obj.click_through_rate

    def save_model(self, request, obj, form, change):
        folder = f"{cld._cfg()['ROOT_FOLDER']}/ads"
        try:
            if form.cleaned_data.get("image_file"):
                obj.image_url, obj.image_public_id = _upload(form.cleaned_data["image_file"], folder)
            if form.cleaned_data.get("logo_file"):
                obj.advertiser_logo_url, _ = _upload(form.cleaned_data["logo_file"], folder)
        except Exception as exc:  # noqa: BLE001 - shown to the admin
            logger.exception("Ad image upload failed")
            messages.error(request, f"Image upload failed: {exc}")
        super().save_model(request, obj, form, change)

    @admin.action(description="Switch on selected ads")
    def activate(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Switch off selected ads")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)
