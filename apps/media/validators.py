import os

from PIL import Image, UnidentifiedImageError
from rest_framework import status

from apps.core.exceptions import ServiceError

from . import cloudinary_service as cld

# Container signatures for the video formats we accept.
_VIDEO_SIGNATURES = (
    (4, b"ftyp"),  # mp4 / mov / m4v / 3gp (ISO BMFF)
    (0, b"\x1a\x45\xdf\xa3"),  # webm / matroska
)


def _is_video(head: bytes) -> bool:
    return any(head[offset : offset + len(sig)] == sig for offset, sig in _VIDEO_SIGNATURES)


def sniff_resource_type(uploaded_file):
    """Identify an upload by its bytes, not its claimed content type."""
    ext = os.path.splitext(uploaded_file.name or "")[1].lower().lstrip(".")
    uploaded_file.seek(0)
    head = uploaded_file.read(32)
    uploaded_file.seek(0)

    if _is_video(head):
        fmt = ext if ext in cld.allowed_formats("video") else "mp4"
        return "video", fmt

    try:
        with Image.open(uploaded_file) as img:
            img.verify()
            fmt = (img.format or "").lower()
    except (UnidentifiedImageError, OSError, SyntaxError):
        if ext in ("heic", "heif"):
            uploaded_file.seek(0)
            return "image", ext
        raise ServiceError(
            "Unsupported file type.", code="unsupported_file_type", status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        ) from None
    finally:
        uploaded_file.seek(0)
    fmt = {"jpeg": "jpg", "mpo": "jpg"}.get(fmt, fmt)
    if fmt not in cld.allowed_formats("image"):
        raise ServiceError(
            f"Unsupported image format: {fmt}.",
            code="unsupported_file_type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    return "image", fmt
