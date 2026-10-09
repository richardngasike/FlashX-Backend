from django.db import models
from django.utils.html import format_html

from apps.media import cloudinary_service as cld


def thumb(public_id, resource_type="image", size=56):
    if not public_id:
        return "-"
    url = (cld.variants(public_id, resource_type) or {}).get("thumbnail")
    if not url:
        return "-"
    return format_html(
        '<img src="{}" style="width:{}px;height:{}px;object-fit:cover;border-radius:6px;" loading="lazy">',
        url,
        size,
        size,
    )


def preview(public_id, resource_type="image"):
    if not public_id:
        return "-"
    v = cld.variants(public_id, resource_type) or {}
    if resource_type == "video":
        return format_html(
            '<video src="{}" poster="{}" controls style="max-width:360px;border-radius:8px;"></video>',
            v.get("url_sd"),
            v.get("poster"),
        )
    return format_html('<img src="{}" style="max-width:360px;border-radius:8px;">', v.get("medium"))


# Admin URL inputs default to https:// when a scheme is missing (Django 6.0 behaviour).
URL_FIELD_OVERRIDES = {models.URLField: {"assume_scheme": "https"}}
