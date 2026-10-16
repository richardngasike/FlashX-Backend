import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, InterfaceError, OperationalError, connection
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

    def __init__(self, detail=None, code=None, status_code=None, details=None):
        if status_code is not None:
            self.status_code = status_code
        # Extra data for the client (e.g. a balance), returned as error.details.
        self.extra_details = details
        super().__init__(detail, code)


class MediaServiceUnavailable(ServiceError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "media_service_unavailable"
    default_detail = "Uploads are temporarily unavailable. Try again shortly."


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


UNAVAILABLE_MESSAGE = "We couldn't complete that right now. Please try again in a moment."


def _discard_connection():
    # Inside a transaction the atomic block's exit cleans up instead.
    if connection.in_atomic_block:
        return
    try:
        connection.close()
    except Exception:  # noqa: BLE001 - already broken; nothing else to do
        pass


def _unavailable_response():
    response = Response(
        {"success": False, "error": {"code": "unavailable", "message": UNAVAILABLE_MESSAGE, "details": None}},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
    response["Retry-After"] = "2"
    # Read by DatabaseRetryMiddleware, which repeats safe requests once.
    response.db_unavailable = True
    return response


def flashx_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        if isinstance(exc, (OperationalError, InterfaceError)):
            # Lost or refused database connection (pooler full, network blip,
            # cold start). Drop the broken connection so the next request
            # opens a fresh one, and tell the client to retry shortly.
            logger.error("Database unavailable: %s: %s", type(exc).__name__, exc)
            _discard_connection()
            return _unavailable_response()
        if isinstance(exc, DatabaseError):
            logger.exception("Database error", exc_info=exc)
            _discard_connection()
        else:
            logger.exception("Unhandled API error", exc_info=exc)
        # Never describe the failure to the client.
        return Response(
            {
                "success": False,
                "error": {"code": "unavailable", "message": UNAVAILABLE_MESSAGE, "details": None},
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
    # DRF/SimpleJWT wrap their own errors as {"detail", "code", "messages"}; those
    # carry nothing beyond the message. A form field named "code" is kept.
    if isinstance(details, dict) and "detail" in details and set(details.keys()) <= {"detail", "code", "messages"}:
        details = None

    extra = getattr(exc, "extra_details", None)
    if extra is not None:
        details = extra
    payload = {"success": False, "error": {"code": code, "message": str(message), "details": details}}
    if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
        payload["error"]["retry_after"] = int(exc.wait)
    response.data = payload
    return response
