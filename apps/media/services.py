import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.exceptions import ServiceError

from . import cloudinary_service as cld
from .models import PURPOSE_RESOURCE_TYPES, MediaAsset, MediaPurpose, ResourceType

logger = logging.getLogger(__name__)


def _limits():
    return settings.FLASHX_MEDIA_LIMITS


def max_duration_for(purpose: str):
    limits = _limits()
    return {
        MediaPurpose.POST: limits["POST_VIDEO_MAX_SECONDS"],
        MediaPurpose.STORY: limits["STORY_VIDEO_MAX_SECONDS"],
        MediaPurpose.REEL: limits["REEL_MAX_SECONDS"],
        MediaPurpose.MESSAGE: limits["MESSAGE_VIDEO_MAX_SECONDS"],
        MediaPurpose.SOUND: limits.get("SOUND_MAX_SECONDS", 600),
    }.get(purpose)


def check_purpose(purpose: str, resource_type: str):
    allowed = PURPOSE_RESOURCE_TYPES.get(purpose)
    if allowed is None:
        raise ServiceError("Unknown media purpose.", code="invalid_purpose")
    if resource_type not in allowed:
        raise ServiceError(
            f"{purpose} does not accept {resource_type} files.",
            code="unsupported_file_type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )


def file_extension(filename: str, resource_type: str, purpose: str) -> str:
    """Extension for a raw upload, checked against the allowed document types ("" otherwise)."""
    if resource_type != ResourceType.RAW:
        return ""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in cld.allowed_formats(resource_type, purpose):
        raise ServiceError(
            "This file type can't be sent.",
            code="unsupported_file_type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    return ext


def validate_metadata(meta: dict, purpose: str):
    """Raise ServiceError if the asset breaks size/format/duration rules."""
    limits = _limits()
    rtype = meta["resource_type"]
    fmt = (meta.get("format") or "").lower()
    if rtype == ResourceType.RAW and not fmt:
        # Cloudinary reports no format for raw files: use the extension.
        fmt = (meta.get("public_id") or "").rsplit(".", 1)[-1].lower() if "." in (meta.get("public_id") or "") else ""
        if not fmt:
            raise ServiceError(
                "Unsupported file type.",
                code="unsupported_file_type",
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
    if fmt and fmt not in cld.allowed_formats(rtype, purpose):
        raise ServiceError(
            f"Unsupported {rtype} format: {fmt}.",
            code="unsupported_file_type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    max_bytes = {
        ResourceType.VIDEO: limits["VIDEO_MAX_BYTES"],
        ResourceType.RAW: limits["FILE_MAX_BYTES"],
    }.get(rtype, limits["IMAGE_MAX_BYTES"])
    if meta.get("bytes", 0) > max_bytes:
        raise ServiceError(
            f"File too large. Maximum is {max_bytes // (1024 * 1024)} MB.",
            code="file_too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    if rtype == ResourceType.VIDEO:
        max_seconds = max_duration_for(purpose)
        if purpose == MediaPurpose.MESSAGE and cld.is_audio_format(fmt):
            # Voice notes and audio files: longer than chat videos.
            max_seconds = limits.get("MESSAGE_AUDIO_MAX_SECONDS", 1800)
        duration = meta.get("duration")
        if max_seconds and duration and duration > max_seconds + 0.5:
            raise ServiceError(f"Video is too long. Maximum is {max_seconds} seconds.", code="video_too_long")


def _create_asset(owner, purpose, meta, *, is_managed=True) -> MediaAsset:
    return MediaAsset.objects.create(
        owner=owner,
        purpose=purpose,
        resource_type=meta["resource_type"],
        public_id=meta["public_id"],
        version=meta.get("version") or "",
        format=meta.get("format") or "",
        secure_url=meta.get("secure_url") or "",
        bytes=meta.get("bytes") or 0,
        width=meta.get("width"),
        height=meta.get("height"),
        duration=meta.get("duration"),
        is_managed=is_managed,
    )


EFFECT_TEXT_STYLES = {"clean", "bold", "neon", "type", "outline", "serif", "script"}
EFFECT_ALIGNS = {"left", "center", "right"}
MAX_EFFECT_TEXTS = 12


def _num(value, low, high, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(low, min(high, v))


def clean_effects(raw) -> dict:
    """
    Keep only the parts of an editor look the app knows how to draw:
    ``{"filter": slug, "texts": [{text, style, color, x, y, size, rotation, align}]}``.
    Positions and sizes are fractions of the video frame. Anything else is dropped.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    slug = raw.get("filter")
    if isinstance(slug, str) and slug and len(slug) <= 32 and slug.replace("_", "").replace("-", "").isalnum():
        out["filter"] = slug.lower()
    strength = raw.get("strength")
    if strength is not None and "filter" in out:
        out["strength"] = round(_num(strength, 0, 1, 1), 3)
    texts = []
    for t in raw.get("texts") or []:
        if not isinstance(t, dict) or len(texts) >= MAX_EFFECT_TEXTS:
            continue
        text = str(t.get("text") or "").strip()[:200]
        if not text:
            continue
        color = str(t.get("color") or "FFFFFFFF").upper().lstrip("#")
        if len(color) == 6:
            color = "FF" + color
        if len(color) != 8 or any(c not in "0123456789ABCDEF" for c in color):
            color = "FFFFFFFF"
        style = t.get("style") if t.get("style") in EFFECT_TEXT_STYLES else "clean"
        align = t.get("align") if t.get("align") in EFFECT_ALIGNS else "center"
        texts.append(
            {
                "text": text,
                "style": style,
                "color": color,
                "align": align,
                "x": round(_num(t.get("x"), 0, 1, 0.5), 4),
                "y": round(_num(t.get("y"), 0, 1, 0.5), 4),
                "size": round(_num(t.get("size"), 0.02, 0.4, 0.075), 4),
                "rotation": round(_num(t.get("rotation"), -6.3, 6.3, 0), 4),
            }
        )
    if texts:
        out["texts"] = texts
    return out


def register_direct_upload(
    user, *, purpose, resource_type, public_id, version, signature, client_meta=None, effects=None
):
    """
    Register a file the client uploaded straight to Cloudinary using
    parameters from ``sign_upload``. Ownership is proven by the folder path
    (only the server can sign uploads into it) and by Cloudinary's response
    signature. Metadata comes from the Admin API when verification is on.
    """
    check_purpose(purpose, resource_type)
    if not cld.belongs_to(public_id, user.id, purpose):
        raise ServiceError("This upload does not belong to you.", code="upload_not_owned", status_code=403)

    existing = MediaAsset.objects.filter(public_id=public_id).first()
    if existing:
        if existing.owner_id != user.id:
            raise ServiceError("This upload does not belong to you.", code="upload_not_owned", status_code=403)
        if effects is not None and not existing.is_attached:
            existing.effects = clean_effects(effects)
            existing.save(update_fields=["effects"])
        return existing

    if not cld.verify_response_signature(public_id, version, signature):
        raise ServiceError("Upload signature is invalid.", code="invalid_upload_signature", status_code=403)

    if settings.CLOUDINARY["VERIFY_WITH_ADMIN_API"]:
        meta = cld.fetch_resource(public_id, resource_type)
    else:
        client_meta = client_meta or {}
        meta = {
            "public_id": public_id,
            "version": str(version),
            "resource_type": resource_type,
            "format": (client_meta.get("format") or "").lower(),
            "secure_url": client_meta.get("secure_url") or "",
            "bytes": int(client_meta.get("bytes") or 0),
            "width": client_meta.get("width"),
            "height": client_meta.get("height"),
            "duration": client_meta.get("duration"),
        }
    try:
        validate_metadata(meta, purpose)
    except ServiceError:
        cld.destroy(public_id, resource_type)
        raise
    asset = _create_asset(user, purpose, meta)
    cleaned = clean_effects(effects)
    if cleaned:
        asset.effects = cleaned
        asset.save(update_fields=["effects"])
    return asset


def upload_from_request(user, *, purpose, uploaded_file) -> MediaAsset:
    """Server-side upload of a multipart file, validated before it leaves the server."""
    from .validators import sniff_resource_type

    resource_type, fmt = sniff_resource_type(uploaded_file)
    check_purpose(purpose, resource_type)
    validate_metadata(
        {"resource_type": resource_type, "format": fmt, "bytes": uploaded_file.size, "duration": None}, purpose
    )
    uploaded_file.seek(0)
    meta = cld.upload_file(uploaded_file, user.id, purpose, resource_type)
    try:
        validate_metadata(meta, purpose)
    except ServiceError:
        cld.destroy(meta["public_id"], resource_type)
        raise
    return _create_asset(user, purpose, meta)


def claim_assets(user, asset_ids, *, purposes, max_items=None) -> list[MediaAsset]:
    """
    Lock and attach the given assets to new content. Order of ``asset_ids``
    is preserved. Must run inside the caller's transaction.
    """
    ids = list(dict.fromkeys(int(i) for i in asset_ids))
    if not ids:
        return []
    if max_items and len(ids) > max_items:
        raise ServiceError(f"You can attach up to {max_items} files.", code="too_many_media")
    assets = {a.id: a for a in MediaAsset.objects.select_for_update().filter(id__in=ids, owner=user, is_attached=False)}
    missing = [i for i in ids if i not in assets]
    if missing:
        raise ServiceError("One or more files are unavailable or already used.", code="media_unavailable")
    ordered = [assets[i] for i in ids]
    for asset in ordered:
        if asset.purpose not in purposes:
            raise ServiceError(f"File {asset.id} was uploaded for {asset.purpose}.", code="media_purpose_mismatch")
    now = timezone.now()
    MediaAsset.objects.filter(id__in=ids).update(is_attached=True, attached_at=now)
    for asset in ordered:
        asset.is_attached, asset.attached_at = True, now
    return ordered


def claim_one(user, asset_id, *, purposes) -> MediaAsset:
    return claim_assets(user, [asset_id], purposes=purposes)[0]


def release_asset(asset_id):
    """Delete an asset row; the post_delete signal removes the Cloudinary file after commit."""
    if asset_id:
        for asset in MediaAsset.objects.filter(pk=asset_id):
            asset.delete()


def purge_orphans(older_than_hours=24, batch_size=200, dry_run=False) -> int:
    """
    Delete uploads never attached to content. Each row's post_delete signal
    removes the Cloudinary file. Works in id batches so it is safe behind a
    transaction-mode connection pooler.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    qs = MediaAsset.objects.filter(is_attached=False, created_at__lt=cutoff)
    if dry_run:
        return qs.count()
    total = 0
    while True:
        ids = list(qs.order_by("id").values_list("id", flat=True)[:batch_size])
        if not ids:
            return total
        with transaction.atomic():
            for asset in MediaAsset.objects.filter(id__in=ids):
                asset.delete()
        total += len(ids)
