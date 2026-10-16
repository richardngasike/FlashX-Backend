"""The shared "security" cache, with a safe fallback.

Rate limits and sign-in lockouts live in the "security" cache (a database
table in production). If that table is missing (the core.0001 migration has
not run yet) or the database hiccups, counting falls back to the per-process
cache for that call instead of failing the request: protection is reduced
for a moment, but nobody is locked out of the app.
"""

import logging

from django.core.cache import caches
from django.db import DatabaseError, transaction

logger = logging.getLogger(__name__)


class _SafeCache:
    def __init__(self, alias="security", fallback="default"):
        self._alias = alias
        self._fallback = fallback

    def __getattr__(self, name):
        primary = getattr(caches[self._alias], name)
        if not callable(primary):
            return primary

        def call(*args, **kwargs):
            try:
                # A savepoint keeps a failed cache query from breaking the
                # surrounding transaction (if there is one).
                with transaction.atomic():
                    return primary(*args, **kwargs)
            except DatabaseError as exc:
                logger.error(
                    "Security cache unavailable (%s: %s); using the local cache. Run `manage.py migrate`.",
                    type(exc).__name__,
                    exc,
                )
                return getattr(caches[self._fallback], name)(*args, **kwargs)

        return call

    @property
    def backend(self):
        return caches[self._alias]


security_cache = _SafeCache()
