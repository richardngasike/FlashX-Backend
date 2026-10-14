"""
GIF creator and library.

* Upload: a GIF file the user already has (validated like any upload).
* From a video: Cloudinary renders the chosen segment as a GIF (size, frame
  rate, speed and an optional caption), then the result is copied into the
  owner's folder as an ordinary image.
* From photos: each photo is fetched from Cloudinary already letterboxed to
  the same size (and captioned), and the frames are assembled here with
  Pillow, in the order chosen, with the chosen frame time and looping.

The finished file is always a stored image in the owner's folder, so it is
deleted with the GIF and can be checked by URL when someone uses it.
"""

import io
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q

from apps.core.exceptions import ServiceError
from apps.media import cloudinary_service as cld
from apps.media.models import MediaAsset, MediaPurpose, ResourceType
from apps.media.services import claim_assets, claim_one

from .models import Gif

logger = logging.getLogger(__name__)

WIDTHS = (240, 320, 480)
FPS = (5, 8, 10, 12, 15)
SPEEDS = (0.5, 1.0, 1.5, 2.0)
TEXT_MAX = 60
FETCH_TIMEOUT = 15


def _limits():
    limits = settings.FLASHX_MEDIA_LIMITS
    return (
        limits.get("GIF_MAX_BYTES", 8 * 1024 * 1024),
        limits.get("GIF_VIDEO_MAX_SECONDS", 10),
        limits.get("GIF_MAX_FRAMES", 30),
    )


def _clean_text(text) -> str:
    text = " ".join((text or "").split())
    if len(text) > TEXT_MAX:
        raise ServiceError(f"Keep the caption under {TEXT_MAX} characters.", code="validation_error")
    return text


def _check_size(meta) -> None:
    max_bytes, _, _ = _limits()
    if meta.get("bytes", 0) > max_bytes:
        cld.destroy(meta["public_id"], "image")
        raise ServiceError(
            f"That GIF would be over {max_bytes // (1024 * 1024)} MB. "
            "Choose a shorter part, fewer photos or a smaller size.",
            code="gif_too_large",
        )


def _discard_sources(assets) -> None:
    """The photos or video a GIF was made from aren't kept."""
    for asset in assets:
        cld.destroy(asset.public_id, asset.resource_type)
    MediaAsset.objects.filter(pk__in=[a.pk for a in assets]).delete()


def _create(owner, meta, source, *, title="", is_public=False) -> Gif:
    return Gif.objects.create(
        owner=owner,
        title=(title or "").strip()[:80],
        source=source,
        public_id=meta["public_id"],
        url=meta["secure_url"],
        width=meta.get("width"),
        height=meta.get("height"),
        bytes=meta.get("bytes") or 0,
        frames=meta.get("pages"),
        is_public=bool(is_public),
    )


@transaction.atomic
def create_from_upload(owner, *, media_id, title="", is_public=False) -> Gif:
    asset = claim_one(owner, media_id, purposes={MediaPurpose.GIF})
    if asset.resource_type != ResourceType.IMAGE or (asset.format or "").lower() != "gif":
        raise ServiceError("Choose a GIF file.", code="unsupported_file_type")
    max_bytes, _, _ = _limits()
    if asset.bytes > max_bytes:
        raise ServiceError(f"GIFs can be up to {max_bytes // (1024 * 1024)} MB.", code="gif_too_large")
    gif = Gif.objects.create(
        owner=owner,
        title=(title or "").strip()[:80],
        source=Gif.Source.UPLOAD,
        public_id=asset.public_id,
        url=asset.secure_url,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        is_public=bool(is_public),
    )
    # The GIF row now owns the file; the asset row is no longer needed.
    MediaAsset.objects.filter(pk=asset.pk).delete()
    return gif


@transaction.atomic
def create_from_video(
    owner, *, media_id, start, end, width=320, fps=10, speed=1.0, text="", title="", is_public=False
) -> Gif:
    _, max_seconds, _ = _limits()
    if width not in WIDTHS or fps not in FPS or speed not in SPEEDS:
        raise ServiceError("Choose a supported size, frame rate and speed.", code="validation_error")
    asset = claim_one(owner, media_id, purposes={MediaPurpose.GIF})
    if asset.resource_type != ResourceType.VIDEO or cld.is_audio_format(asset.format):
        raise ServiceError("Choose a video.", code="unsupported_file_type")
    start, end = float(start), float(end)
    total = asset.duration or 0
    if start < 0 or end <= start:
        raise ServiceError("The end must be after the start.", code="validation_error")
    if total and start >= total:
        raise ServiceError("The start is past the end of the video.", code="validation_error")
    if total:
        end = min(end, total)
    if end - start > max_seconds + 0.05:
        raise ServiceError(f"A GIF can be at most {max_seconds} seconds of video.", code="gif_too_long")
    if end - start < 0.3:
        raise ServiceError("Choose a longer part of the video.", code="validation_error")
    url = cld.video_gif_url(
        asset.public_id, start=start, end=end, width=width, fps=fps, speed=speed, text=_clean_text(text)
    )
    meta = cld.store_remote_image(url, owner.pk, MediaPurpose.GIF)
    _check_size(meta)
    gif = _create(owner, meta, Gif.Source.VIDEO, title=title, is_public=is_public)
    transaction.on_commit(lambda: _discard_sources([asset]))
    return gif


def _fetch(url) -> bytes:
    import requests

    r = requests.get(url, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    return r.content


def assemble_gif(frames: list, *, delay_ms: int, loop: bool) -> bytes:
    """Frames (PNG/JPEG bytes, same size) to an animated GIF."""
    from PIL import Image

    images = []
    for data in frames:
        with Image.open(io.BytesIO(data)) as im:
            images.append(im.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT))
    buf = io.BytesIO()
    options = {"save_all": True, "append_images": images[1:], "duration": delay_ms, "optimize": True, "disposal": 2}
    if loop:
        options["loop"] = 0  # forever; without it the GIF plays once
    images[0].save(buf, format="GIF", **options)
    return buf.getvalue()


@transaction.atomic
def create_from_images(
    owner, *, media_ids, delay_ms=500, width=320, aspect="square", loop=True, text="", title="", is_public=False
) -> Gif:
    _, _, max_frames = _limits()
    ids = list(media_ids or [])
    if len(ids) < 2:
        raise ServiceError("Choose at least two photos.", code="validation_error")
    if len(ids) > max_frames:
        raise ServiceError(f"A GIF can have up to {max_frames} photos.", code="validation_error")
    if len(set(ids)) != len(ids):
        raise ServiceError("Each photo can be used once.", code="validation_error")
    if width not in WIDTHS or not (60 <= int(delay_ms) <= 3000):
        raise ServiceError("Choose a supported size and frame time.", code="validation_error")
    height = {"square": width, "portrait": round(width * 5 / 4), "landscape": round(width * 9 / 16)}.get(aspect)
    if height is None:
        raise ServiceError("Choose square, portrait or landscape.", code="validation_error")
    assets = claim_assets(owner, ids, purposes={MediaPurpose.GIF})
    if any(a.resource_type != ResourceType.IMAGE for a in assets):
        raise ServiceError("Only photos can be made into a GIF.", code="unsupported_file_type")
    caption = _clean_text(text)
    try:
        frames = [_fetch(cld.gif_frame_url(a.public_id, width=width, height=height, text=caption)) for a in assets]
        data = assemble_gif(frames, delay_ms=int(delay_ms), loop=bool(loop))
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - network or image errors
        logger.warning("GIF assembly failed: %s", exc)
        raise ServiceError("Couldn't make the GIF. Please try again.", code="gif_failed") from exc
    max_bytes, _, _ = _limits()
    if len(data) > max_bytes:
        raise ServiceError("That GIF would be too large. Use fewer photos or a smaller size.", code="gif_too_large")
    meta = cld.upload_image_bytes(data, owner.pk, MediaPurpose.GIF, filename=f"{uuid.uuid4().hex}.gif")
    meta.setdefault("pages", len(frames))
    if not meta.get("pages"):
        meta["pages"] = len(frames)
    gif = _create(owner, meta, Gif.Source.IMAGES, title=title, is_public=is_public)
    transaction.on_commit(lambda: _discard_sources(assets))
    return gif


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
def my_gifs(user, query=""):
    qs = Gif.objects.filter(owner=user)
    if query:
        qs = qs.filter(title__icontains=query)
    return qs


def public_gifs(viewer, query=""):
    """Other people's public GIFs (not from blocked or private accounts)."""
    from apps.blocks.selectors import exclude_blocked

    qs = Gif.objects.filter(is_public=True, owner__is_active=True, owner__is_private=False).select_related("owner")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(owner__username__icontains=query))
    return exclude_blocked(qs, viewer, "owner_id").order_by("-uses_count", "-created_at", "-id")


def visible_gif(viewer, gif_id) -> Gif:
    from django.http import Http404

    gif = Gif.objects.select_related("owner").filter(pk=gif_id).first()
    if gif is None or (gif.owner_id != viewer.pk and not gif.is_public):
        raise Http404
    return gif


def update_gif(gif, actor, data) -> Gif:
    if gif.owner_id != actor.pk:
        raise ServiceError("You can only change your own GIFs.", code="permission_denied", status_code=403)
    fields = []
    if "title" in data:
        gif.title = (data["title"] or "").strip()[:80]
        fields.append("title")
    if "is_public" in data:
        gif.is_public = bool(data["is_public"])
        fields.append("is_public")
    if fields:
        gif.save(update_fields=fields)
    return gif


def delete_gif(gif, actor) -> None:
    """Deletes the file too; where it was already used it shows as unavailable."""
    if gif.owner_id != actor.pk:
        raise ServiceError("You can only delete your own GIFs.", code="permission_denied", status_code=403)
    public_id = gif.public_id
    gif.delete()
    transaction.on_commit(lambda: cld.destroy(public_id, "image"))


def usable_gif(user, url):
    """The library GIF at ``url`` if ``user`` may send it (their own, or public), else None."""
    gif = Gif.objects.filter(url=url).first()
    if gif is None or (gif.owner_id != user.pk and not gif.is_public):
        return None
    Gif.objects.filter(pk=gif.pk).update(uses_count=F("uses_count") + 1)
    return gif


def payload(gif, viewer=None) -> dict:
    return {
        "id": gif.pk,
        "title": gif.title,
        "url": gif.url,
        # Small animated version for pickers, and a still for slow connections.
        "preview": cld.image_url(gif.public_id, width=240),
        "still": cld._build(gif.public_id, "image", width=240, crop="limit", format="jpg"),
        "width": gif.width,
        "height": gif.height,
        "bytes": gif.bytes,
        "frames": gif.frames,
        "source": gif.source,
        "is_public": gif.is_public,
        "uses_count": gif.uses_count,
        "is_owner": viewer is not None and gif.owner_id == viewer.pk,
        "owner": {"id": gif.owner_id, "username": gif.owner.username} if gif.owner_id else None,
        "created_at": gif.created_at.isoformat() if gif.created_at else None,
    }
