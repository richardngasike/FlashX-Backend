import hmac

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from .cache import security_cache


class SharedCacheMixin:
    """
    Count in the shared "security" cache (the database in production) instead
    of each server instance's memory. On serverless hosting every instance
    has its own memory, so a per-process limit is easy to get around by
    spreading requests out; the shared count holds across all of them.
    """

    @property
    def cache(self):
        return security_cache


class ProxyAwareIdentMixin:
    """
    The FlashX website calls the API from its own server, so every web
    visitor would share that server's IP and its anonymous rate limits. When
    the request carries the website's shared secret, throttle by the visitor
    IP the website reports instead. Without the secret the header is ignored,
    so clients cannot spoof their IP.
    """

    def get_ident(self, request):
        secret = getattr(settings, "WEB_PROXY_SECRET", "")
        sent = request.META.get("HTTP_X_FLASHX_PROXY", "")
        client_ip = request.META.get("HTTP_X_FLASHX_CLIENT_IP", "").strip()
        if secret and sent and client_ip and hmac.compare_digest(sent, secret):
            return client_ip[:64]
        return super().get_ident(request)


class FlashXAnonRateThrottle(ProxyAwareIdentMixin, AnonRateThrottle):
    pass


class FlashXUserRateThrottle(ProxyAwareIdentMixin, UserRateThrottle):
    pass


class AuthRateThrottle(SharedCacheMixin, FlashXAnonRateThrottle):
    """Signing in, per IP. Generous, because mobile networks put many people
    behind one address; per-account lockout (users.services) stops guessing."""

    scope = "login"


class RegisterThrottle(SharedCacheMixin, FlashXAnonRateThrottle):
    scope = "register"


class TokenRefreshThrottle(FlashXAnonRateThrottle):
    scope = "token_refresh"


class LookupThrottle(FlashXAnonRateThrottle):
    """Username availability checks while typing."""

    scope = "lookup"


class PasswordResetThrottle(SharedCacheMixin, FlashXAnonRateThrottle):
    scope = "password_reset"


class UploadThrottle(SharedCacheMixin, FlashXUserRateThrottle):
    scope = "upload"


class ContentCreateThrottle(FlashXUserRateThrottle):
    """Counts creating content only: reading (GET) a comments list must not use up the quota."""

    scope = "content_create"

    def allow_request(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().allow_request(request, view)


class PaymentThrottle(SharedCacheMixin, FlashXUserRateThrottle):
    """Starting payments (each one sends a PIN prompt to a phone)."""

    scope = "payment"


class MessageSendThrottle(FlashXUserRateThrottle):
    scope = "message_send"


class ReportThrottle(SharedCacheMixin, FlashXUserRateThrottle):
    scope = "report"


class ReactionThrottle(FlashXUserRateThrottle):
    scope = "reaction"


class TypingThrottle(FlashXUserRateThrottle):
    scope = "typing"


class GifSearchThrottle(FlashXUserRateThrottle):
    scope = "gif_search"


class LiveActionThrottle(FlashXUserRateThrottle):
    """Host and viewer actions in a live room: requests, invites, polls, moderation."""

    scope = "live_action"
