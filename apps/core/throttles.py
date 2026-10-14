import hmac

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


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


class AuthRateThrottle(FlashXAnonRateThrottle):
    scope = "auth"


class PasswordResetThrottle(FlashXAnonRateThrottle):
    scope = "password_reset"


class UploadThrottle(FlashXUserRateThrottle):
    scope = "upload"


class ContentCreateThrottle(FlashXUserRateThrottle):
    """Counts creating content only: reading (GET) a comments list must not use up the quota."""

    scope = "content_create"

    def allow_request(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().allow_request(request, view)


class MessageSendThrottle(FlashXUserRateThrottle):
    scope = "message_send"


class ReportThrottle(FlashXUserRateThrottle):
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
