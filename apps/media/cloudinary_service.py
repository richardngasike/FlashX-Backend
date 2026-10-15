"""
Thin, mockable wrapper around the Cloudinary SDK.

Nothing outside this module talks to Cloudinary directly. The API secret
never leaves the server: clients receive short-lived signed parameters.
"""

import logging
import time
import uuid

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from django.conf import settings

from apps.core.exceptions import MediaServiceUnavailable, ServiceError

logger = logging.getLogger(__name__)

_configured = False


def _cfg():
    return settings.CLOUDINARY


def is_configured() -> bool:
    c = _cfg()
    return bool(c["CLOUD_NAME"] and c["API_KEY"] and c["API_SECRET"])


def configure():
    global _configured
    if _configured:
        return
    c = _cfg()
    cloudinary.config(cloud_name=c["CLOUD_NAME"], api_key=c["API_KEY"], api_secret=c["API_SECRET"], secure=True)
    _configured = True


def require_configured():
    if not is_configured():
        raise MediaServiceUnavailable()
    configure()


def folder_for(user_id: int, purpose: str) -> str:
    return f"{_cfg()['ROOT_FOLDER']}/u{user_id}/{purpose}"


def new_public_id(user_id: int, purpose: str) -> str:
    return f"{folder_for(user_id, purpose)}/{uuid.uuid4().hex}"


def belongs_to(public_id: str, user_id: int, purpose: str) -> bool:
    return public_id.startswith(folder_for(user_id, purpose) + "/")


def allowed_formats(resource_type: str, purpose: str = "") -> list[str]:
    limits = settings.FLASHX_MEDIA_LIMITS
    if resource_type == "raw":
        return limits["FILE_FORMATS"]
    if resource_type == "video":
        # Audio files and voice notes are "video" resources on Cloudinary; messages and sounds take them.
        return limits["VIDEO_FORMATS"] + (limits["AUDIO_FORMATS"] if purpose in ("message", "sound") else [])
    return limits["IMAGE_FORMATS"]


def is_audio_format(fmt: str) -> bool:
    return (fmt or "").lower() in settings.FLASHX_MEDIA_LIMITS["AUDIO_FORMATS"]


def sign_upload(user_id: int, purpose: str, resource_type: str, extension: str = "") -> dict:
    """Signed params for a direct client -> Cloudinary upload into the user's own folder."""
    require_configured()
    c = _cfg()
    public_id = new_public_id(user_id, purpose)
    if resource_type == "raw" and extension:
        # Raw files keep their extension in the public id, so downloads open in the right app.
        public_id = f"{public_id}.{extension}"
    params = {
        "public_id": public_id,
        "timestamp": int(time.time()),
        "overwrite": "false",
    }
    if resource_type != "raw":
        # Cloudinary checks raw formats by extension only; the API re-checks after upload.
        params["allowed_formats"] = ",".join(allowed_formats(resource_type, purpose))
    signature = cloudinary.utils.api_sign_request(params, c["API_SECRET"])
    return {
        **params,
        "signature": signature,
        "api_key": c["API_KEY"],
        "cloud_name": c["CLOUD_NAME"],
        "resource_type": resource_type,
        "upload_url": f"https://api.cloudinary.com/v1_1/{c['CLOUD_NAME']}/{resource_type}/upload",
        "expires_in": c["SIGNATURE_TTL_SECONDS"],
    }


def verify_response_signature(public_id: str, version, signature: str) -> bool:
    require_configured()
    try:
        return cloudinary.utils.verify_api_response_signature(public_id, version, signature)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Cloudinary signature verification failed")
        return False


def _extract_duration(res: dict):
    if res.get("duration") is not None:
        return float(res["duration"])
    fmt = (res.get("video_metadata") or {}).get("format") or {}
    if fmt.get("duration") is not None:
        try:
            return float(fmt["duration"])
        except (TypeError, ValueError):
            return None
    return None


def fetch_resource(public_id: str, resource_type: str) -> dict:
    """Authoritative metadata from the Cloudinary Admin API."""
    require_configured()
    try:
        res = cloudinary.api.resource(public_id, resource_type=resource_type)
    except cloudinary.exceptions.NotFound:
        raise ServiceError("That upload could not be found. Please upload it again.", code="upload_not_found") from None
    except cloudinary.exceptions.Error as exc:
        logger.warning("Cloudinary resource lookup failed: %s", exc)
        raise MediaServiceUnavailable() from exc
    return normalize(res, resource_type)


def normalize(res: dict, resource_type: str) -> dict:
    return {
        "public_id": res["public_id"],
        "version": str(res.get("version", "")),
        "format": (res.get("format") or "").lower(),
        "secure_url": res.get("secure_url") or "",
        "bytes": int(res.get("bytes") or 0),
        "width": res.get("width"),
        "height": res.get("height"),
        "duration": _extract_duration(res) if resource_type == "video" else None,
        "resource_type": resource_type,
        # Frames in an animated image (GIF), when Cloudinary reports it.
        "pages": res.get("pages"),
    }


def upload_file(fileobj, user_id: int, purpose: str, resource_type: str) -> dict:
    """Server-side upload (used for multipart uploads from the app)."""
    require_configured()
    public_id = new_public_id(user_id, purpose)
    try:
        if resource_type == "video":
            res = cloudinary.uploader.upload_large(
                fileobj, public_id=public_id, resource_type="video", overwrite=False, chunk_size=20 * 1024 * 1024
            )
        else:
            res = cloudinary.uploader.upload(fileobj, public_id=public_id, resource_type="image", overwrite=False)
    except cloudinary.exceptions.Error as exc:
        logger.warning("Cloudinary upload failed: %s", exc)
        raise ServiceError("Upload failed. Please try again.", code="upload_failed") from exc
    return normalize(res, resource_type)


def upload_remote(url: str, public_id: str, resource_type: str) -> dict:
    require_configured()
    res = cloudinary.uploader.upload(url, public_id=public_id, resource_type=resource_type, overwrite=True)
    return normalize(res, resource_type)


def store_remote_image(url: str, user_id: int, purpose: str) -> dict:
    """Copy an image (e.g. a GIF Cloudinary just rendered) into the user's folder as a normal upload."""
    require_configured()
    try:
        res = cloudinary.uploader.upload(
            url, public_id=new_public_id(user_id, purpose), resource_type="image", overwrite=False
        )
    except cloudinary.exceptions.Error as exc:
        logger.warning("Cloudinary remote upload failed: %s", exc)
        raise ServiceError("Couldn't make the GIF. Try a shorter or smaller one.", code="gif_failed") from exc
    return normalize(res, "image")


def _text_layer(text: str) -> dict:
    return {
        "overlay": {"font_family": "Arial", "font_size": 34, "font_weight": "bold", "text": text},
        "color": "#FFFFFF",
        "gravity": "south",
        "y": 18,
    }


def video_gif_url(public_id, *, start, end, width, fps, speed=1.0, text=""):
    """An animated GIF of part of an uploaded video (rendered by Cloudinary on request)."""
    step = {"start_offset": f"{start:.2f}", "end_offset": f"{end:.2f}", "width": width, "crop": "limit", "fps": fps}
    if speed and abs(speed - 1.0) > 0.01:
        # accelerate:100 doubles the speed, -50 halves it.
        step["effect"] = f"accelerate:{int(round((speed - 1.0) * 100))}"
    transformation = [step]
    if text:
        transformation.append(_text_layer(text))
    return _build(public_id, "video", transformation=transformation, format="gif")


def gif_frame_url(public_id, *, width, height, text=""):
    """One photo as a same-size GIF frame (letterboxed on black), optionally captioned."""
    transformation = [{"width": width, "height": height, "crop": "pad", "background": "black"}]
    if text:
        transformation.append(_text_layer(text))
    return _build(public_id, "image", transformation=transformation, format="png")


def upload_image_bytes(data: bytes, user_id: int, purpose: str, filename="image.gif") -> dict:
    """Store image bytes made on the server (e.g. a GIF assembled from photos) in the user's folder."""
    import io

    require_configured()
    buf = io.BytesIO(data)
    buf.name = filename
    try:
        res = cloudinary.uploader.upload(
            buf, public_id=new_public_id(user_id, purpose), resource_type="image", overwrite=False
        )
    except cloudinary.exceptions.Error as exc:
        logger.warning("Cloudinary upload failed: %s", exc)
        raise ServiceError("Couldn't save the GIF. Please try again.", code="gif_failed") from exc
    return normalize(res, "image")


def list_resources(prefix: str, resource_type: str, max_results: int = 100) -> list[dict]:
    require_configured()
    res = cloudinary.api.resources(type="upload", prefix=prefix, resource_type=resource_type, max_results=max_results)
    return [normalize(r, resource_type) for r in res.get("resources", [])]


def destroy(public_id: str, resource_type: str) -> bool:
    if not is_configured():
        return False
    configure()
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type=resource_type, invalidate=True)
        return result.get("result") in ("ok", "not found")
    except Exception:
        logger.exception("Cloudinary destroy failed for %s", public_id)
        return False


# ---------------------------------------------------------------------------
# Delivery URLs (no network calls; pure URL building)
# ---------------------------------------------------------------------------
def _build(public_id, resource_type, **options):
    if not public_id:
        return None
    configure() if is_configured() else None
    cloud = _cfg()["CLOUD_NAME"] or "flashx"
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, resource_type=resource_type, secure=True, cloud_name=cloud, **options
    )
    return url


def image_url(public_id, width=None, height=None, crop="limit", gravity=None):
    opts = {"fetch_format": "auto", "quality": "auto", "crop": crop}
    if width:
        opts["width"] = width
    if height:
        opts["height"] = height
    if gravity:
        opts["gravity"] = gravity
    return _build(public_id, "image", **opts)


def video_url(public_id, width=None, quality="auto"):
    opts = {"quality": quality, "format": "mp4", "video_codec": "auto"}
    if width:
        opts.update(width=width, crop="limit")
    return _build(public_id, "video", **opts)


def video_poster_url(public_id, width=720):
    return _build(public_id, "video", format="jpg", start_offset="0", width=width, crop="limit", quality="auto")


def audio_url(public_id, start=None, end=None):
    """
    The audio of an uploaded video or audio file as MP3 (used for reusable
    sounds), optionally only the part between ``start`` and ``end`` seconds.
    """
    opts = {"format": "mp3", "audio_codec": "mp3", "bit_rate": "128k"}
    if start:
        opts["start_offset"] = f"{float(start):.2f}"
    if end:
        opts["end_offset"] = f"{float(end):.2f}"
    return _build(public_id, "video", **opts)


def original_url(public_id, resource_type):
    """The file as uploaded, without resizing or recompression (used for downloads)."""
    return _build(public_id, resource_type)


def variants(public_id, resource_type):
    """URL set the Flutter client uses for responsive delivery."""
    if not public_id:
        return None
    if resource_type == "raw":
        # Documents: served as uploaded.
        url = original_url(public_id, "raw")
        return {"url": url, "original": url}
    if resource_type == "video":
        return {
            "url": video_url(public_id),
            "url_sd": video_url(public_id, width=540),
            # Data saver and slow connections.
            "url_low": video_url(public_id, width=360, quality="auto:eco"),
            "poster": video_poster_url(public_id),
            "thumbnail": video_poster_url(public_id, width=360),
            "original": original_url(public_id, "video"),
        }
    return {
        "url": image_url(public_id, width=1440),
        "medium": image_url(public_id, width=720),
        "thumbnail": image_url(public_id, width=360, height=360, crop="fill", gravity="auto"),
        "original": original_url(public_id, "image"),
    }
