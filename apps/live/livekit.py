"""
LiveKit integration: access tokens (JWT signed with the API secret) and the
one server call we need (changing a guest's publish permission).

Configure LIVEKIT_URL (wss://<project>.livekit.cloud), LIVEKIT_API_KEY and
LIVEKIT_API_SECRET. Without them live video is switched off.
"""

import json
import logging
import time

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 6 * 60 * 60


def is_configured() -> bool:
    c = settings.LIVEKIT
    return bool(c["URL"] and c["API_KEY"] and c["API_SECRET"])


def server_url() -> str:
    return settings.LIVEKIT["URL"]


def access_token(
    *,
    room: str,
    identity: str,
    name: str,
    can_publish: bool,
    admin: bool = False,
    metadata=None,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    now = int(time.time())
    grant = {
        "room": room,
        "roomJoin": True,
        "canPublish": can_publish,
        "canSubscribe": True,
        "canPublishData": True,
    }
    if admin:
        grant["roomAdmin"] = True
    claims = {
        "iss": settings.LIVEKIT["API_KEY"],
        "sub": identity,
        "name": name,
        "nbf": now - 10,
        "exp": now + ttl_seconds,
        "video": grant,
    }
    if metadata:
        claims["metadata"] = json.dumps(metadata)
    return jwt.encode(claims, settings.LIVEKIT["API_SECRET"], algorithm="HS256")


def _admin_token(room: str) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.LIVEKIT["API_KEY"],
        "sub": "flashx-server",
        "nbf": now - 10,
        "exp": now + 300,
        # DeleteRoom needs roomCreate; UpdateParticipant needs roomAdmin.
        "video": {"room": room, "roomAdmin": True, "roomCreate": True},
    }
    return jwt.encode(claims, settings.LIVEKIT["API_SECRET"], algorithm="HS256")


def _call(room: str, method: str, body: dict) -> bool:
    """Best-effort RoomService call. Live keeps working (minus the server action) if LiveKit is unreachable."""
    if not is_configured():
        return False
    import requests

    base = server_url().replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    try:
        r = requests.post(
            f"{base}/twirp/livekit.RoomService/{method}",
            json=body,
            headers={"Authorization": f"Bearer {_admin_token(room)}"},
            timeout=5,
        )
        if r.status_code != 200:
            logger.warning("LiveKit %s failed: %s %s", method, r.status_code, r.text[:200])
        return r.status_code == 200
    except requests.RequestException as exc:
        logger.warning("LiveKit %s failed: %s", method, exc)
        return False


def set_can_publish(room: str, identity: str, can_publish: bool, *, microphone: bool = True) -> bool:
    """
    Change what a participant may publish. ``can_publish=False`` takes a guest
    off screen; ``microphone=False`` keeps their camera but mutes them (LiveKit
    unpublishes tracks from sources that are no longer allowed).
    """
    permission = {"canSubscribe": True, "canPublish": can_publish, "canPublishData": True}
    if can_publish and not microphone:
        permission["canPublishSources"] = ["CAMERA", "SCREEN_SHARE", "SCREEN_SHARE_AUDIO"]
    return _call(room, "UpdateParticipant", {"room": room, "identity": identity, "permission": permission})


def remove_participant(room: str, identity: str) -> bool:
    """Disconnect someone from the room. Used with bans: the API then refuses them a new connection."""
    return _call(room, "RemoveParticipant", {"room": room, "identity": identity})


def end_room(room: str) -> bool:
    return _call(room, "DeleteRoom", {"room": room})
