from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthRateThrottle(AnonRateThrottle):
    scope = "auth"


class PasswordResetThrottle(AnonRateThrottle):
    scope = "password_reset"


class UploadThrottle(UserRateThrottle):
    scope = "upload"


class ContentCreateThrottle(UserRateThrottle):
    scope = "content_create"


class MessageSendThrottle(UserRateThrottle):
    scope = "message_send"


class ReportThrottle(UserRateThrottle):
    scope = "report"
