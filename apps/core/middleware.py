import logging
import time

logger = logging.getLogger(__name__)

# Requests that change nothing (or nothing that matters if repeated) and so
# can be run again when the first try could not reach the database.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SAFE_POST_PATHS = frozenset({"/api/auth/login/"})
# Polled every few seconds by the app; the next poll is the retry, and
# repeating them would only add load while connections are scarce.
_POLLED_SUFFIXES = ("/typing/", "/unread-count/")


class DatabaseRetryMiddleware:
    """Run a safe request once more when the database connection failed.

    Serverless instances often find their pooled connection gone (the pooler
    recycled it, a cold start raced the network). The API exception handler
    discards the broken connection and marks the response; one quick second
    try opens a fresh connection, so the person never sees the hiccup.
    """

    delay_seconds = 0.25

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(response, "db_unavailable", False) and self._repeatable(request):
            logger.warning("Retrying %s %s after a database connection failure", request.method, request.path)
            time.sleep(self.delay_seconds)
            response = self.get_response(request)
        return response

    @staticmethod
    def _repeatable(request):
        if request.path.endswith(_POLLED_SUFFIXES):
            return False
        return request.method in _SAFE_METHODS or (request.method == "POST" and request.path in _SAFE_POST_PATHS)
