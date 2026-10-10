"""
Real-time sync events.

Every change that other people's screens should reflect at once (a new or
deleted message, a group change, a call, a live update) is sent to the
affected users' phones as a silent FCM data message: ``{"sync": "<event>",
...ids}``. The app treats it as "refresh this", then fetches the actual data
through the authenticated API, so a payload never carries content the user
is not allowed to read. Events are delivered after the database commit, and
the app de-duplicates them and also refreshes on resume, which covers missed
or repeated deliveries.
"""

import logging

from django.db import transaction

from . import push

logger = logging.getLogger(__name__)


def _send(user_ids, data, collapse_key, ttl_seconds, high_priority):
    try:
        push.send_data_to_users(
            user_ids, data=data, collapse_key=collapse_key, ttl_seconds=ttl_seconds, high_priority=high_priority
        )
    except Exception:  # noqa: BLE001 - sync is best effort
        logger.exception("Sync event %s failed", data.get("sync"))


def emit(user_ids, sync_event, *, collapse_key="", ttl_seconds=60, high_priority=True, **data):
    """Queue ``sync_event`` for ``user_ids`` after the current transaction commits."""
    ids = sorted({int(u) for u in user_ids if u})
    if not ids or not push.is_enabled():
        return
    payload = {"sync": sync_event, **{k: v for k, v in data.items() if v is not None}}
    transaction.on_commit(lambda: _send(ids, payload, collapse_key, ttl_seconds, high_priority))
