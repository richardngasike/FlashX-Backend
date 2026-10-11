"""
Sounds for posts and reels.

Three kinds of audio are offered:

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

* **Openverse music** (api.openverse.org, run by WordPress): openly licensed
  songs indexed from Jamendo, ccMixter, Freesound, Wikimedia and others. Works
  without a key (optional OPENVERSE_CLIENT_ID / OPENVERSE_CLIENT_SECRET raise
  the rate limit). Only CC0, public domain, CC BY and CC BY-SA tracks are
  offered (plus NC when commercial use is off), streamed from the source with
  the credit line the licence requires.

* **Original sounds**: the soundtrack of a FlashX video, reusable by others
  unless the owner turns that off.

YouTube, Spotify and store previews (Deezer, iTunes) are deliberately not
used: none of them licenses its audio for putting under user videos, and the
YouTube terms forbid separating audio from its videos.
"""

import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Q

from apps.core.exceptions import ServiceError

from .models import USABLE_SOURCES, Sound

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


def openverse_enabled() -> bool:
    return bool(_cfg().get("OPENVERSE_ENABLED", True))


def provider_names() -> list:
    names = []
    if jamendo_enabled():
        names.append("jamendo")
    if openverse_enabled():
        names.append("openverse")
    return names + ["original"]


def catalogue_enabled() -> bool:
    return jamendo_enabled() or openverse_enabled()


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
# Openverse
# ---------------------------------------------------------------------------
OPENVERSE_API = "https://api.openverse.org/v1"
OPENVERSE_PAGE_MAX = 20  # anonymous page size limit
OPENVERSE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Formats both Android and iOS play. Openverse lists Jamendo's MP3 streams as
# "mp31"/"mp32" with URLs that have no file extension (".../?trackid=1&format=mp31").
PLAYABLE_TYPES = {"mp3", "mp31", "mp32", "mpeg", "m4a", "aac", "wav"}
OPENVERSE_TIMEOUT = 9
STALE_SECONDS = 7 * 24 * 3600
LICENCE_NAMES = {
    "cc0": "CC0",
    "pdm": "Public domain",
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "by-nc": "CC BY-NC",
    "by-nc-sa": "CC BY-NC-SA",
}
SOURCE_NAMES = {
    "jamendo": "Jamendo",
    "ccmixter": "ccMixter",
    "freesound": "Freesound",
    "wikimedia_audio": "Wikimedia Commons",
    "freemusicarchive": "Free Music Archive",
}


def _openverse_licences() -> list:
    allowed = ["cc0", "pdm", "by", "by-sa"]
    if not _cfg().get("COMMERCIAL_USE", True):
        allowed += ["by-nc", "by-nc-sa"]
    return allowed


def _openverse_token() -> str:
    """Bearer token when OPENVERSE_CLIENT_ID/SECRET are set (higher rate limit), else anonymous."""
    client_id, secret = _cfg().get("OPENVERSE_CLIENT_ID"), _cfg().get("OPENVERSE_CLIENT_SECRET")
    if not (client_id and secret):
        return ""
    token = cache.get("music:openverse:token")
    if token:
        return token
    import requests

    try:
        r = requests.post(
            f"{OPENVERSE_API}/auth_tokens/token/",
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 - fall back to anonymous access
        logger.warning("Openverse token request failed: %s", exc)
        return ""
    token = data.get("access_token") or ""
    if token:
        cache.set("music:openverse:token", token, max(60, int(data.get("expires_in") or 3600) - 120))
    return token


def _openverse(path, params=None):
    import requests

    headers = {"User-Agent": "FlashX/1.0 (social app; sounds for user posts)"}
    token = _openverse_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{OPENVERSE_API}{path}", params=params or {}, headers=headers, timeout=OPENVERSE_TIMEOUT)
    if r.status_code >= 400:
        # Kept for the health check: 429 means the anonymous rate limit (set OPENVERSE_CLIENT_ID/SECRET).
        cache.set("music:openverse:last_error", f"HTTP {r.status_code}", 24 * 3600)
    r.raise_for_status()
    return r.json()


def _filetype(item) -> str:
    """The audio format, from Openverse's field or, failing that, the URL."""
    from urllib.parse import parse_qs, urlparse

    ft = (item.get("filetype") or "").lower().strip()
    if ft:
        return ft
    parts = urlparse(item.get("url") or "")
    last = parts.path.rsplit("/", 1)[-1]
    if "." in last:
        return last.rsplit(".", 1)[-1].lower()
    return (parse_qs(parts.query).get("format") or [""])[0].lower()


def _openverse_ok(item) -> bool:
    if (item.get("license") or "").lower() not in _openverse_licences():
        return False
    if item.get("mature"):
        return False
    url = item.get("url") or ""
    return url.startswith("https://") and _filetype(item) in PLAYABLE_TYPES


def _openverse_payload(item) -> dict:
    licence = (item.get("license") or "").lower()
    version = item.get("license_version") or ""
    name = LICENCE_NAMES.get(licence, licence.upper())
    if version and licence not in ("cc0", "pdm"):
        name = f"{name} {version}"
    source = SOURCE_NAMES.get(item.get("source") or "", (item.get("source") or "Openverse").replace("_", " ").title())
    title = (item.get("title") or "Untitled").strip()
    artist = (item.get("creator") or "").strip()
    duration = item.get("duration")
    genres = item.get("genres") or []
    cover = item.get("thumbnail") or ((item.get("audio_set") or {}).get("thumbnail")) or ""
    return {
        "id": f"openverse:{item.get('id')}",
        "source": "openverse",
        "title": title,
        "artist": artist,
        "cover": cover,
        "audio_url": item.get("url") or "",
        "duration": round(duration / 1000, 1) if isinstance(duration, (int, float)) and duration else None,
        "genre": genres[0] if genres else "",
        "license_name": name,
        "license_url": item.get("license_url") or "",
        "link": item.get("foreign_landing_url") or "",
        "attribution": f"{title} by {artist or 'unknown artist'} ({source}, {name})",
        "uses_count": 0,
        "is_original": False,
    }


def _search_openverse(query, genre, limit) -> list:
    if not openverse_enabled():
        return []
    terms = " ".join(t for t in (query, genre) if t) or "music"
    params = {
        "q": terms,
        "category": "music",
        "license": ",".join(_openverse_licences()),
        "page_size": min(OPENVERSE_PAGE_MAX, max(limit, 1) * 2),
    }
    key = "music:openverse:" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    cached = cache.get(key)
    if cached is not None:
        return cached[:limit]
    try:
        rows = _openverse("/audio/", params).get("results") or []
        tracks = [_openverse_payload(r) for r in rows if _openverse_ok(r)]
    except Exception as exc:  # noqa: BLE001 - the picker still offers other sounds
        logger.warning("Openverse search failed: %s", exc)
        # Rate limited or down: the last good answer for this search is better than nothing.
        return (cache.get(key + ":stale") or [])[:limit]
    cache.set(key, tracks, CACHE_SECONDS)
    if tracks:
        cache.set(key + ":stale", tracks, STALE_SECONDS)
        cache.delete("music:openverse:last_error")
    return tracks[:limit]


def catalogue_status() -> dict:
    """For the health check: which song sources are on and the last Openverse error, if any."""
    return {
        "providers": provider_names(),
        "openverse_authenticated": bool(_cfg().get("OPENVERSE_CLIENT_ID") and _cfg().get("OPENVERSE_CLIENT_SECRET")),
        "openverse_last_error": cache.get("music:openverse:last_error"),
    }


def _fetch_openverse_track(track_id) -> dict:
    if not openverse_enabled():
        raise ServiceError("Music is not available right now.", code="music_unavailable", status_code=503)
    try:
        item = _openverse(f"/audio/{track_id}/")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Openverse lookup failed: %s", exc)
        raise ServiceError("Couldn't load that song. Try again.", code="music_unavailable", status_code=503) from exc
    if not _openverse_ok(item):
        raise ServiceError("That song can't be used.", code="sound_unavailable")
    return item


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------
def _stored_attribution(sound: Sound) -> str:
    if sound.source == Sound.Source.JAMENDO:
        return f"{sound.title} by {sound.artist} (Jamendo)"
    if sound.source == Sound.Source.OPENVERSE:
        return f"{sound.title} by {sound.artist or 'unknown artist'} ({sound.license_name})"
    return sound.label


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
        "attribution": _stored_attribution(sound),
        "uses_count": sound.uses_count,
        "is_original": sound.source == Sound.Source.FLASHX,
        "owner_id": sound.owner_id,
        "usable": sound.is_active and sound.source in USABLE_SOURCES,
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

    qs = Sound.objects.filter(is_active=True, uses_count__gt=0, source__in=USABLE_SOURCES)
    qs = exclude_blocked(qs.filter(Q(owner__isnull=True) | Q(owner__is_private=False)), viewer, "owner_id")
    return list(qs.order_by("-uses_count", "-created_at")[:limit])


def search(viewer, query="", genre="", limit=30) -> dict:
    query = (query or "").strip()[:100]
    genre = (genre or "").strip()[:40]
    catalogue = _search_jamendo(query, genre, limit)
    if len(catalogue) < limit:
        # Openverse also indexes Jamendo: skip songs already listed.
        seen = {(t["title"].lower(), t["artist"].lower()) for t in catalogue}
        for t in _search_openverse(query, genre, limit):
            if (t["title"].lower(), t["artist"].lower()) not in seen:
                seen.add((t["title"].lower(), t["artist"].lower()))
                catalogue.append(t)
        catalogue = catalogue[:limit]
    # Tracks already used on FlashX resolve to their stored id so counts and pages line up.
    if catalogue:
        by_source = {}
        for t in catalogue:
            source, external = t["id"].split(":", 1)
            by_source.setdefault(source, []).append(external)
        stored = {}
        for source, ids in by_source.items():
            for snd in Sound.objects.filter(source=source, external_id__in=ids):
                stored[f"{source}:{snd.external_id}"] = snd
        for t in catalogue:
            snd = stored.get(t["id"])
            if snd is not None:
                t["id"] = str(snd.pk)
                t["uses_count"] = snd.uses_count
    originals = [] if genre else [sound_payload(s) for s in original_sounds(viewer, query, limit=10)]
    popular = [] if (query or genre) else [sound_payload(s) for s in popular_sounds(viewer, limit=10)]
    return {"catalogue": catalogue, "original": originals, "popular": popular, "catalogue_enabled": catalogue_enabled()}


# ---------------------------------------------------------------------------
# Attaching sounds to content
# ---------------------------------------------------------------------------
def resolve(user, sound_id) -> Sound:
    """
    Turn the id the app sends ("123" for a stored sound, "jamendo:456" or
    "openverse:<uuid>" for a catalogue track) into a usable Sound. Catalogue
    details are re-read from the provider so the client can't attach
    arbitrary audio or a track whose licence doesn't allow it.
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
        return _store_catalogue(Sound.Source.JAMENDO, track_id, _track_payload(_fetch_jamendo_track(track_id)))
    if raw.startswith("openverse:"):
        track_id = raw.split(":", 1)[1].lower()
        if not OPENVERSE_ID.match(track_id):
            raise ServiceError("That song can't be used.", code="sound_unavailable")
        existing = Sound.objects.filter(source=Sound.Source.OPENVERSE, external_id=track_id).first()
        if existing is not None:
            if not existing.is_active:
                raise ServiceError("That song can't be used.", code="sound_unavailable")
            return existing
        return _store_catalogue(Sound.Source.OPENVERSE, track_id, _openverse_payload(_fetch_openverse_track(track_id)))
    if not raw.isdigit():
        raise ServiceError("That sound can't be used.", code="sound_unavailable")
    sound = Sound.objects.filter(pk=int(raw)).select_related("owner").first()
    usable = sound is not None and sound.is_active and sound.source in USABLE_SOURCES
    if not usable:
        raise ServiceError("That sound can't be used.", code="sound_unavailable")
    if sound.owner_id and sound.owner_id != user.pk:
        from apps.blocks.selectors import is_blocked_between

        if not sound.owner.is_active or sound.owner.is_private or is_blocked_between(user, sound.owner):
            raise ServiceError("That sound can't be used.", code="sound_unavailable")
    return sound


def _store_catalogue(source, external_id, p) -> Sound:
    sound, _ = Sound.objects.get_or_create(
        source=source,
        external_id=external_id,
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
