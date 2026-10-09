import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ServiceError(exceptions.APIException):
    """Raised by service-layer code for domain rule violations."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_request"
    default_detail = "The request could not be completed."

    def __init__(self, detail=None, code=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail, code)


class MediaServiceUnavailable(ServiceError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "media_service_unavailable"
    default_detail = "The media service is temporarily unavailable. Try again shortly."


_CODE_MAP = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "file_too_large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
}


def _first_message(details):
    if isinstance(details, str):
        return details
    if isinstance(details, list) and details:
        return _first_message(details[0])
    if isinstance(details, dict) and details:
        key, value = next(iter(details.items()))
        msg = _first_message(value)
        if key in ("detail", "non_field_errors"):
            return msg
        return f"{key}: {msg}" if msg else None
    return None


def flashx_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API error", exc_info=exc)
        return Response(
            {
                "success": False,
                "error": {"code": "server_error", "message": "Something went wrong on our side.", "details": None},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    details = response.data
    code = None
    if isinstance(exc, exceptions.APIException):
        codes = exc.get_codes()
        if isinstance(codes, str):
            code = codes
    if not code or code in ("invalid", "error"):
        code = _CODE_MAP.get(response.status_code, "error")
    if isinstance(exc, exceptions.ValidationError):
        code = "validation_error"

    message = _first_message(details) or "Request failed."
    if isinstance(details, dict) and set(details.keys()) <= {"detail", "code", "messages"}:
        details = None

    payload = {"success": False, "error": {"code": code, "message": str(message), "details": details}}
    if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
        payload["error"]["retry_after"] = int(exc.wait)
    response.data = payload
    return response
