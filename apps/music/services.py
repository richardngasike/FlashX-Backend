"""
Sounds for posts and reels.

Two kinds of audio are offered:

* **Catalogue tracks from Jamendo** (independent artists, every track under a
  Creative Commons licence). Tracks are streamed from Jamendo, never copied
  or offered for offline download, and every use carries the attribution the
  licence and the Jamendo API terms ask for: artist, track, licence link and a
  backlink to the track page, with Jamendo credited as the provider.
  Tracks whose licence forbids derivatives (CC ND) are never offered, because
  putting music under a video is a derivative work. When
  ``MUSIC["COMMERCIAL_USE"]`` is on (FlashX shows sponsored posts, which counts
  as commercial use), NonCommercial (CC NC) tracks are excluded too, and the
  Jamendo API itself must be used under a commercial agreement with Jamendo.
  Set ``JAMENDO_CLIENT_ID`` to switch the catalogue on.

* **Original sounds**: the soundtrack of a FlashX video, reusable by others
  unless the owner turns that off.

YouTube, Spotify and store previews (Deezer, iTunes) are deliberately not
used: none of them licenses its audio for putting under user videos.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Q

from apps.core.exceptions import ServiceError

from .models import Sound

logger = logging.getLogger(__name__)

JAMENDO_API = "https://api.jamendo.com/v3.0"
TIMEOUT = 6
CACHE_SECONDS = 600

# Genre chips in the picker (Jamendo tag names).
GENRES = [
    ("pop", "Pop"),
    ("hiphop", "Hip hop"),
    ("rnb", "R&B"),
    ("electronic", "Electronic"),
    ("dance", "Dance"),
    ("rock", "Rock"),
    ("reggae", "Reggae"),
    ("world", "World"),
    ("jazz", "Jazz"),
    ("lounge", "Chill"),
    ("acoustic", "Acoustic"),
    ("classical", "Classical"),
    ("soundtrack", "Cinematic"),
    ("ambient", "Ambient"),
]


def _cfg():
    return getattr(settings, "MUSIC", {})


def jamendo_enabled() -> bool:
    return bool(_cfg().get("JAMENDO_CLIENT_ID"))


def provider_names() -> list:
    names = ["original"]
    if jamendo_enabled():
        names.insert(0, "jamendo")
    return names


# ---------------------------------------------------------------------------
# Jamendo
# ---------------------------------------------------------------------------
def _jamendo(params) -> list:
    import requests

    query = {
        "client_id": _cfg()["JAMENDO_CLIENT_ID"],
        "format": "json",
        "include": "licenses musicinfo",
        "audioformat": "mp32",
        "imagesize": 300,
        **params,
    }
    r = requests.get(f"{JAMENDO_API}/tracks/", params=query, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    headers = data.get("headers") or {}
    if headers.get("status") not in (None, "success"):
        raise RuntimeError(f"Jamendo error {headers.get('code')}: {headers.get('error_message')}")
    return data.get("results") or []


def _licence_ok(track) -> bool:
    lic = track.get("licenses") or {}
    if str(lic.get("ccnd", "false")).lower() == "true":
        return False
    if _cfg().get("COMMERCIAL_USE", True) and str(lic.get("ccnc", "false")).lower() == "true":
        return False
    return bool(track.get("audio"))


def _licence_name(track) -> str:
    url = track.get("license_ccurl") or ""
    # https://creativecommons.org/licenses/by-sa/3.0/ -> CC BY-SA 3.0
    parts = [p for p in url.split("/") if p]
    if "licenses" in parts:
        i = parts.index("licenses")
        tail = parts[i + 1 : i + 3]
        if tail:
            return "CC " + " ".join(t.upper() for t in tail)
    return "Creative Commons"


def _genre(track) -> str:
    genres = ((track.get("musicinfo") or {}).get("tags") or {}).get("genres") or []
    return genres[0] if genres else ""


def _track_payload(track) -> dict:
    return {
        "id": f"jamendo:{track['id']}",
        "source": "jamendo",
        "title": track.get("name") or "",
        "artist": track.get("artist_name") or "",
        "cover": track.get("image") or track.get("album_image") or "",
        "audio_url": track.get("audio") or "",
        "duration": track.get("duration"),
        "genre": _genre(track),
        "license_name": _licence_name(track),
        "license_url": track.get("license_ccurl") or "",
        "link": track.get("shareurl") or "",
        "attribution": f"{track.get('name')} by {track.get('artist_name')} (Jamendo)",
        "uses_count": 0,
        "is_original": False,
    }


def _search_jamendo(query, genre, limit) -> list:
    if not jamendo_enabled():
        return []
    params = {"limit": min(limit * 2, 100)}
    if query:
        params["search"] = query
        params["boost"] = "popularity_month"
    else:
        params["order"] = "popularity_week"
    if genre:
        params["fuzzytags"] = genre
    key = "music:jamendo:" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    cached = cache.get(key)
    if cached is not None:
        return cached[:limit]
    try:
        tracks = [_track_payload(t) for t in _jamendo(params) if _licence_ok(t)]
    except Exception as exc:  # noqa: BLE001 - the picker still offers original sounds
        logger.warning("Jamendo search failed: %s", exc)
        return []
    cache.set(key, tracks, CACHE_SECONDS)
    return tracks[:limit]


def _fetch_jamendo_track(track_id) -> dict:
    if not jamendo_enabled():
        raise ServiceError("Music is not available right now.", code="music_unavailable", status_code=503)
    try:
        rows = _jamendo({"id": track_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Jamendo lookup failed: %s", exc)
        raise ServiceError("Couldn't load that song. Try again.", code="music_unavailable", status_code=503) from exc
    if not rows or not _licence_ok(rows[0]):
        raise ServiceError("That song can't be used.", code="sound_unavailable")
    return rows[0]


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------
def sound_payload(sound: Sound) -> dict:
    return {
        "id": str(sound.pk),
        "source": sound.source,
        "title": sound.title,
        "artist": sound.artist,
        "cover": sound.cover_url,
        "audio_url": sound.audio_url,
        "duration": sound.duration,
        "genre": sound.genre,
        "license_name": sound.license_name,
        "license_url": sound.license_url,
        "link": sound.link_url,
        "attribution": (
            f"{sound.title} by {sound.artist} (Jamendo)" if sound.source == Sound.Source.JAMENDO else sound.label
        ),
        "uses_count": sound.uses_count,
        "is_original": sound.source == Sound.Source.FLASHX,
        "owner_id": sound.owner_id,
        "usable": sound.is_active and sound.source in (Sound.Source.JAMENDO, Sound.Source.FLASHX),
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def original_sounds(viewer, query="", limit=20):
    from apps.blocks.selectors import exclude_blocked

    qs = Sound.objects.filter(
        source=Sound.Source.FLASHX, is_active=True, owner__is_active=True, owner__is_private=False
    )
    qs = exclude_blocked(qs, viewer, "owner_id")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(artist__icontains=query) | Q(owner__username__icontains=query))
    return list(qs.order_by("-uses_count", "-created_at")[:limit])


def popular_sounds(viewer, limit=20):
    """Most used sounds on FlashX right now (catalogue and original)."""
    from apps.blocks.selectors import exclude_blocked

    qs = Sound.objects.filter(is_active=True, uses_count__gt=0, source__in=[Sound.Source.JAMENDO, Sound.Source.FLASHX])
    qs = exclude_blocked(qs.filter(Q(owner__isnull=True) | Q(owner__is_private=False)), viewer, "owner_id")
    return list(qs.order_by("-uses_count", "-created_at")[:limit])


def search(viewer, query="", genre="", limit=30) -> dict:
    query = (query or "").strip()[:100]
    genre = (genre or "").strip()[:40]
    catalogue = _search_jamendo(query, genre, limit)
    # Tracks already used on FlashX resolve to their stored id so counts and pages line up.
    if catalogue:
        stored = {
            s.external_id: s
            for s in Sound.objects.filter(
                source=Sound.Source.JAMENDO, external_id__in=[t["id"].split(":", 1)[1] for t in catalogue]
            )
        }
        for t in catalogue:
            s = stored.get(t["id"].split(":", 1)[1])
            if s is not None:
                t["id"] = str(s.pk)
                t["uses_count"] = s.uses_count
    originals = [] if genre else [sound_payload(s) for s in original_sounds(viewer, query, limit=10)]
    popular = [] if (query or genre) else [sound_payload(s) for s in popular_sounds(viewer, limit=10)]
    return {"catalogue": catalogue, "original": originals, "popular": popular, "catalogue_enabled": jamendo_enabled()}


# ---------------------------------------------------------------------------
# Attaching sounds to content
# ---------------------------------------------------------------------------
def resolve(user, sound_id) -> Sound:
    """
    Turn the id the app sends ("123" for a stored sound, "jamendo:456" for a
    catalogue track) into a usable Sound. Catalogue details are re-read from
    Jamendo so the client can't attach arbitrary audio.
    """
    raw = str(sound_id or "").strip()
    if not raw:
        raise ServiceError("Choose a sound.", code="sound_unavailable")
    if raw.startswith("jamendo:"):
        track_id = raw.split(":", 1)[1]
        if not track_id.isdigit():
            raise ServiceError("That song can't be used.", code="sound_unavailable")
        existing = Sound.objects.filter(source=Sound.Source.JAMENDO, external_id=track_id).first()
        if existing is not None:
            if not existing.is_active:
                raise ServiceError("That song can't be used.", code="sound_unavailable")
            return existing
        p = _track_payload(_fetch_jamendo_track(track_id))
        sound, _ = Sound.objects.get_or_create(
            source=Sound.Source.JAMENDO,
            external_id=track_id,
            defaults={
                "title": p["title"][:200],
                "artist": p["artist"][:200],
                "cover_url": p["cover"][:500],
                "audio_url": p["audio_url"][:500],
                "duration": p["duration"],
                "genre": p["genre"][:60],
                "license_name": p["license_name"][:80],
                "license_url": p["license_url"][:300],
                "link_url": p["link"][:300],
            },
        )
        return sound
    if not raw.isdigit():
        raise ServiceError("That sound can't be used.", code="sound_unavailable")
    sound = Sound.objects.filter(pk=int(raw)).select_related("owner").first()
    usable = sound is not None and sound.is_active and sound.source in (Sound.Source.JAMENDO, Sound.Source.FLASHX)
    if not usable:
        raise ServiceError("That sound can't be used.", code="sound_unavailable")
    if sound.owner_id and sound.owner_id != user.pk:
        from apps.blocks.selectors import is_blocked_between

        if not sound.owner.is_active or sound.owner.is_private or is_blocked_between(user, sound.owner):
            raise ServiceError("That sound can't be used.", code="sound_unavailable")
    return sound


def count_use(sound):
    if sound is not None:
        Sound.objects.filter(pk=sound.pk).update(uses_count=F("uses_count") + 1)


def release_use(sound_id):
    if sound_id:
        from django.db.models.functions import Greatest

        Sound.objects.filter(pk=sound_id).update(uses_count=Greatest(F("uses_count") - 1, 0))


def create_original(*, owner, public_id, title="", duration=None, post=None, reel=None) -> Sound:
    """The soundtrack of a new video becomes a reusable sound."""
    from apps.media import cloudinary_service as cld

    return Sound.objects.create(
        source=Sound.Source.FLASHX,
        title=(title or "Original sound")[:200],
        artist=owner.username,
        cover_url=(cld.video_poster_url(public_id, width=300) or "")[:500],
        audio_url=cld.audio_url(public_id)[:500],
        duration=duration,
        owner=owner,
        origin_post=post,
        origin_reel=reel,
    )


SECTION_FIELDS = ("sound_start", "sound_volume", "original_volume")


def clean_mix(data) -> dict:
    """Validate where the song starts and the two volumes (0 to 1)."""
    out = {}
    if data.get("sound_start") is not None:
        try:
            out["sound_start"] = max(0.0, min(float(data["sound_start"]), 3600.0))
        except (TypeError, ValueError) as exc:
            raise ServiceError("Invalid song start.", code="validation_error") from exc
    for f in ("sound_volume", "original_volume"):
        if data.get(f) is not None:
            try:
                out[f] = max(0.0, min(float(data[f]), 1.0))
            except (TypeError, ValueError) as exc:
                raise ServiceError("Invalid volume.", code="validation_error") from exc
    return out


def legacy_sound_id(value):
    """The previous app version sent a whole sound object; take its id."""
    if isinstance(value, dict):
        return value.get("id")
    return value
