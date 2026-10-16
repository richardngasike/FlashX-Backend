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


# Paths that serve HTML link previews (apps.core.share).
_SHARE_PREFIXES = ("/p/", "/r/", "/u/", "/l/", "/g/")
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_SHARE_CSP = (
    "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class SecurityHeadersMiddleware:
    """
    Headers Django's SecurityMiddleware doesn't set: a locked-down content
    policy for API and link-preview responses, no browser features, and no
    caching of anything returned to a signed-in request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if path.startswith("/api/") and not path.startswith(("/api/docs/", "/api/schema/")):
            response.setdefault("Content-Security-Policy", _API_CSP)
            if "HTTP_AUTHORIZATION" in request.META and not response.has_header("Cache-Control"):
                response["Cache-Control"] = "no-store"
        elif path.startswith(_SHARE_PREFIXES):
            response.setdefault("Content-Security-Policy", _SHARE_CSP)
        return response


def client_ip(request) -> str:
    """The caller's address, trusting only the hops added by our own proxies."""
    from django.conf import settings

    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    num = getattr(settings, "REST_FRAMEWORK", {}).get("NUM_PROXIES")
    if xff and num:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            return hops[-min(num, len(hops))][:64]
    return request.META.get("REMOTE_ADDR", "")[:64]


class AdminLoginThrottleMiddleware:
    """At most ADMIN_LOGIN_ATTEMPTS admin sign-in attempts per address per window."""

    attempts = 10
    window_seconds = 15 * 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        if request.method == "POST" and request.path == f"/{settings.ADMIN_URL.strip('/')}/login/":
            from django.http import HttpResponse

            from .cache import security_cache as cache

            key = f"admin-login:{client_ip(request)}"
            if not cache.add(key, 1, self.window_seconds):
                try:
                    count = cache.incr(key)
                except ValueError:
                    cache.set(key, 1, self.window_seconds)
                    count = 1
                if count > self.attempts:
                    logger.warning("Admin sign-in attempts limited for %s", client_ip(request))
                    response = HttpResponse("Too many sign-in attempts. Try again later.", status=429)
                    response["Retry-After"] = str(self.window_seconds)
                    return response
        return self.get_response(request)
