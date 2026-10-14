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

from .models import USABLE_SOURCES, SavedSound, Sound

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


def sound_payload(sound: Sound, viewer=None, saved_ids=None) -> dict:
    """
    A sound as the apps see it. ``viewer`` adds is_owner/is_saved; pass
    ``saved_ids`` (a set of sound ids the viewer saved) when listing many.
    A removed sound keeps its name for attribution but has no audio.
    """
    removed = sound.removed_at is not None
    is_owner = bool(viewer is not None and sound.owner_id and sound.owner_id == viewer.pk)
    if saved_ids is not None:
        is_saved = sound.pk in saved_ids
    elif viewer is not None and getattr(viewer, "is_authenticated", False):
        is_saved = SavedSound.objects.filter(user=viewer, sound=sound).exists()
    else:
        is_saved = False
    return {
        "id": str(sound.pk),
        "source": sound.source,
        "title": sound.title,
        "artist": sound.artist,
        "description": sound.description,
        "cover": sound.cover_url,
        "audio_url": "" if removed else sound.audio_url,
        "duration": sound.duration,
        "genre": sound.genre,
        "license_name": sound.license_name,
        "license_url": sound.license_url,
        "link": sound.link_url,
        "attribution": _stored_attribution(sound),
        "uses_count": sound.uses_count,
        "is_original": sound.source == Sound.Source.FLASHX,
        "origin": sound.origin or ("video" if sound.source == Sound.Source.FLASHX else "catalogue"),
        "owner_id": sound.owner_id,
        "owner_username": sound.owner.username if sound.owner_id and sound.owner else "",
        "is_public": sound.is_public,
        # Originals: whether the owner lets others use it.
        "allow_reuse": sound.is_active,
        "usable": sound.is_active and not removed and sound.source in USABLE_SOURCES,
        "available": not removed,
        "is_owner": is_owner,
        "is_saved": is_saved,
    }


def payloads(sounds, viewer) -> list:
    """sound_payload for a list, with one query for the viewer's saves."""
    sounds = list(sounds)
    saved = set()
    if viewer is not None and sounds:
        saved = set(
            SavedSound.objects.filter(user=viewer, sound_id__in=[x.pk for x in sounds]).values_list(
                "sound_id", flat=True
            )
        )
    return [sound_payload(x, viewer, saved) for x in sounds]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def original_sounds(viewer, query="", limit=20):
    from apps.blocks.selectors import exclude_blocked

    qs = Sound.objects.filter(
        source=Sound.Source.FLASHX,
        is_active=True,
        is_public=True,
        removed_at__isnull=True,
        owner__is_active=True,
        owner__is_private=False,
    ).select_related("owner")
    qs = exclude_blocked(qs, viewer, "owner_id")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(artist__icontains=query) | Q(owner__username__icontains=query))
    return list(qs.order_by("-uses_count", "-created_at")[:limit])


def popular_sounds(viewer, limit=20):
    """Most used sounds on FlashX right now (catalogue and original)."""
    from apps.blocks.selectors import exclude_blocked

    qs = Sound.objects.filter(
        is_active=True, is_public=True, removed_at__isnull=True, uses_count__gt=0, source__in=USABLE_SOURCES
    ).select_related("owner")
    qs = exclude_blocked(qs.filter(Q(owner__isnull=True) | Q(owner__is_private=False)), viewer, "owner_id")
    return list(qs.order_by("-uses_count", "-created_at")[:limit])


def _within(duration, min_seconds, max_seconds) -> bool:
    if min_seconds is None and max_seconds is None:
        return True
    if not duration:
        return False
    if min_seconds is not None and duration < min_seconds:
        return False
    return max_seconds is None or duration <= max_seconds


def search(viewer, query="", genre="", limit=30, min_seconds=None, max_seconds=None) -> dict:
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
    saved = set(SavedSound.objects.filter(user=viewer).values_list("sound_id", flat=True)) if viewer else set()
    for t in catalogue:
        t["is_saved"] = t["id"].isdigit() and int(t["id"]) in saved
    catalogue = [t for t in catalogue if _within(t.get("duration"), min_seconds, max_seconds)]
    originals = [] if genre else payloads(original_sounds(viewer, query, limit=10), viewer)
    originals = [o for o in originals if _within(o.get("duration"), min_seconds, max_seconds)]
    browsing = not (query or genre)
    popular = payloads(popular_sounds(viewer, limit=10), viewer) if browsing else []
    trending = payloads(trending_sounds(viewer, limit=10), viewer) if browsing else []
    return {
        "catalogue": catalogue,
        "original": originals,
        "popular": popular,
        "trending": trending,
        "catalogue_enabled": catalogue_enabled(),
    }


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
    if sound is None or sound.removed_at is not None or sound.source not in USABLE_SOURCES:
        raise ServiceError("That sound can't be used.", code="sound_unavailable")
    own = bool(sound.owner_id and sound.owner_id == user.pk)
    if not own:
        if not sound.is_active or not sound.is_public:
            raise ServiceError("That sound can't be used.", code="sound_unavailable")
        if sound.owner_id:
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
        origin=Sound.Origin.VIDEO,
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


# ---------------------------------------------------------------------------
# Library: saved, recently used, trending, your own sounds
# ---------------------------------------------------------------------------
def visible_sound(viewer, sound_id) -> Sound:
    """A sound the viewer may see (private uploads only for their owner; not from blocked accounts)."""
    from django.http import Http404

    sound = Sound.objects.select_related("owner").filter(pk=sound_id).first()
    if sound is None:
        raise Http404
    if sound.owner_id and sound.owner_id != viewer.pk:
        from apps.blocks.selectors import is_blocked_between

        if not sound.owner.is_active or not sound.is_public or is_blocked_between(viewer, sound.owner):
            raise Http404
    return sound


def save_sound(user, sound: Sound) -> bool:
    from django.db import IntegrityError, transaction

    if sound.removed_at is not None:
        raise ServiceError("This sound isn't available any more.", code="sound_unavailable")
    try:
        with transaction.atomic():
            SavedSound.objects.create(user=user, sound=sound)
    except IntegrityError:
        pass
    return True


def unsave_sound(user, sound: Sound) -> bool:
    SavedSound.objects.filter(user=user, sound=sound).delete()
    return False


def saved_sounds(user):
    return (
        Sound.objects.filter(saves__user=user, removed_at__isnull=True)
        .select_related("owner")
        .order_by("-saves__created_at", "-id")
    )


def recent_sounds(user, limit=20) -> list:
    """Sounds the user put on their own posts, reels and stories, most recent first."""
    from apps.posts.models import Post
    from apps.reels.models import Reel
    from apps.stories.models import Story

    rows = []
    for model, owner_field in ((Post, "author"), (Reel, "author"), (Story, "author")):
        rows += list(
            model.objects.filter(**{owner_field: user}, sound__isnull=False)
            .order_by("-created_at")
            .values_list("sound_id", "created_at")[: limit * 2]
        )
    rows.sort(key=lambda r: r[1], reverse=True)
    ordered = []
    for sound_id, _ in rows:
        if sound_id not in ordered:
            ordered.append(sound_id)
        if len(ordered) >= limit:
            break
    by_id = {
        x.pk: x
        for x in Sound.objects.filter(pk__in=ordered, removed_at__isnull=True).select_related("owner")
        if x.is_active or x.owner_id == user.pk
    }
    return [by_id[i] for i in ordered if i in by_id]


def trending_sounds(viewer, limit=20, days=7) -> list:
    """Sounds used most on new posts, reels and stories over the last few days."""
    from collections import Counter
    from datetime import timedelta

    from django.utils import timezone

    from apps.blocks.selectors import exclude_blocked
    from apps.posts.models import Post
    from apps.reels.models import Reel
    from apps.stories.models import Story

    since = timezone.now() - timedelta(days=days)
    counts = Counter()
    for model in (Post, Reel, Story):
        for sound_id in model.objects.filter(created_at__gte=since, sound__isnull=False).values_list(
            "sound_id", flat=True
        )[:5000]:
            counts[sound_id] += 1
    if not counts:
        return []
    qs = Sound.objects.filter(
        pk__in=list(counts), is_active=True, is_public=True, removed_at__isnull=True, source__in=USABLE_SOURCES
    ).select_related("owner")
    qs = exclude_blocked(
        qs.filter(Q(owner__isnull=True) | Q(owner__is_private=False, owner__is_active=True)), viewer, "owner_id"
    )
    found = list(qs)
    found.sort(key=lambda x: (-counts[x.pk], -x.uses_count))
    return found[:limit]


def my_sounds(user):
    """Your original sounds (from your videos and uploads), including private and switched-off ones."""
    return (
        Sound.objects.filter(owner=user, source=Sound.Source.FLASHX, removed_at__isnull=True)
        .select_related("owner")
        .order_by("-created_at", "-id")
    )


def _clip_bounds(start, end, file_seconds):
    limit = float(settings.FLASHX_MEDIA_LIMITS.get("SOUND_CLIP_MAX_SECONDS", 180))
    try:
        start = max(0.0, float(start or 0))
        end = float(end) if end not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ServiceError("Invalid start or end.", code="validation_error") from exc
    total = float(file_seconds or 0) or None
    if total is not None and start >= total:
        raise ServiceError("The start is past the end of the audio.", code="validation_error")
    if end is None:
        end = min(total, start + limit) if total else start + limit
    if total is not None:
        end = min(end, total)
    if end - start < 1:
        raise ServiceError("Choose at least one second of audio.", code="validation_error")
    if end - start > limit + 0.5:
        raise ServiceError(f"A sound can be at most {int(limit)} seconds.", code="sound_too_long")
    return round(start, 2), round(end, 2)


def create_uploaded(
    owner,
    *,
    media_id,
    title,
    description="",
    start=0,
    end=None,
    cover_media_id=None,
    is_public=True,
    allow_reuse=True,
) -> Sound:
    """
    Publish an original sound from an audio file (or a video's soundtrack)
    the user uploaded or recorded. Only the chosen part is played.
    """
    from django.db import transaction

    from apps.media import cloudinary_service as cld
    from apps.media.models import MediaPurpose, ResourceType
    from apps.media.services import claim_one

    title = (title or "").strip()
    if not title:
        raise ServiceError("Give your sound a name.", code="validation_error")
    with transaction.atomic():
        asset = claim_one(owner, media_id, purposes={MediaPurpose.SOUND})
        if asset.resource_type != ResourceType.VIDEO:
            raise ServiceError("Choose an audio file.", code="unsupported_file_type")
        cover = None
        if cover_media_id:
            cover = claim_one(owner, cover_media_id, purposes={MediaPurpose.SOUND})
            if cover.resource_type != ResourceType.IMAGE:
                raise ServiceError("The cover must be an image.", code="unsupported_file_type")
        start, end = _clip_bounds(start, end, asset.duration)
        is_audio = cld.is_audio_format(asset.format) or not asset.width
        if cover is not None:
            cover_url = cld.image_url(cover.public_id, width=300, height=300, crop="fill")
        elif not is_audio:
            cover_url = cld.video_poster_url(asset.public_id, width=300) or ""
        else:
            cover_url = ""
        return Sound.objects.create(
            source=Sound.Source.FLASHX,
            origin=Sound.Origin.UPLOAD,
            title=title[:200],
            artist=owner.username,
            description=(description or "").strip()[:300],
            cover_url=(cover_url or "")[:500],
            audio_url=cld.audio_url(asset.public_id, start=start if start else None, end=end)[:500],
            duration=round(end - start, 2),
            owner=owner,
            asset=asset,
            cover_asset=cover,
            is_public=bool(is_public),
            is_active=bool(allow_reuse),
        )


def update_sound(sound: Sound, actor, data) -> Sound:
    if sound.owner_id != actor.pk:
        raise ServiceError("You can only change your own sounds.", code="permission_denied", status_code=403)
    if sound.removed_at is not None:
        raise ServiceError("This sound was removed.", code="sound_unavailable")
    fields = []
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise ServiceError("Give your sound a name.", code="validation_error")
        sound.title = title[:200]
        fields.append("title")
    if "description" in data:
        sound.description = (data["description"] or "").strip()[:300]
        fields.append("description")
    if "allow_reuse" in data:
        sound.is_active = bool(data["allow_reuse"])
        fields.append("is_active")
        # Keep the video's own switch in step for sounds that came from a video.
        for model in ("origin_post", "origin_reel"):
            content = getattr(sound, model, None)
            if content is not None and content.allow_sound_reuse != sound.is_active:
                type(content).objects.filter(pk=content.pk).update(allow_sound_reuse=sound.is_active)
    if "is_public" in data:
        if sound.origin != Sound.Origin.UPLOAD and not data["is_public"]:
            raise ServiceError("Sounds from your videos follow the video's privacy.", code="validation_error")
        sound.is_public = bool(data["is_public"])
        fields.append("is_public")
    if fields:
        sound.save(update_fields=fields)
    return sound


def remove_sound(sound: Sound, actor) -> None:
    """
    The owner deletes a sound. Uploaded sounds stop playing everywhere
    (posts that used them show the sound as unavailable); a video's sound
    just stops being offered, since it is still the video's own audio.
    """
    from django.utils import timezone

    if sound.owner_id != actor.pk:
        raise ServiceError("You can only delete your own sounds.", code="permission_denied", status_code=403)
    if sound.origin != Sound.Origin.UPLOAD:
        update_sound(sound, actor, {"allow_reuse": False})
        return
    sound.removed_at = timezone.now()
    sound.removed_reason = "Deleted by its owner"
    sound.is_active = False
    sound.save(update_fields=["removed_at", "removed_reason", "is_active"])
    SavedSound.objects.filter(sound=sound).delete()
