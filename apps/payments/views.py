import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils.crypto import constant_time_compare
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.phone import display_phone, normalize_ke_phone
from apps.core.throttles import PaymentThrottle

from . import services
from .models import MpesaPayment

logger = logging.getLogger(__name__)


class PhoneField(serializers.CharField):
    """A Kenyan mobile number in any common form, returned as 2547XXXXXXXX."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return normalize_ke_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0]) from None


class VerificationPaySerializer(serializers.Serializer):
    phone = PhoneField(max_length=20)


class VerificationView(APIView):
    """
    GET  /api/payments/verification/  price, whether it can be paid now, your
         saved number and the latest attempt.
    POST /api/payments/verification/ {phone}  sends the M-PESA PIN prompt for
         the verified badge; then poll GET /api/payments/{id}/.
    """

    serializer_class = VerificationPaySerializer

    def get_throttles(self):
        return [PaymentThrottle()] if self.request.method == "POST" else super().get_throttles()

    def get(self, request):
        services.refresh_pending(request.user, MpesaPayment.Purpose.VERIFICATION)
        request.user.refresh_from_db(fields=["is_verified"])
        latest = MpesaPayment.objects.filter(user=request.user, purpose=MpesaPayment.Purpose.VERIFICATION).first()
        return Response(
            {
                "is_verified": request.user.is_verified,
                "price": services.verification_price(),
                "currency": "KES",
                "available": services.mpesa_available(),
                "phone": display_phone(request.user.phone_number),
                "latest": services.payload(latest) if latest else None,
            }
        )

    def post(self, request):
        s = VerificationPaySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payment = services.start_verification(request.user, s.validated_data["phone"])
        return Response(services.payload(payment), status=status.HTTP_201_CREATED)


class PaymentDetailView(APIView):
    """GET /api/payments/{id}/ — status of one of your payments (checks with M-PESA when it is late)."""

    serializer_class = VerificationPaySerializer

    def get(self, request, pk):
        payment = get_object_or_404(MpesaPayment, pk=pk, user=request.user)
        return Response(services.payload(services.refresh(payment)))


class MpesaCallbackView(APIView):
    """
    POST /api/payments/mpesa/callback/{token}/ — Safaricom's result for an STK
    push. Not for clients: the token is a server secret.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []
    serializer_class = None

    def post(self, request, token):
        expected = (getattr(settings, "MPESA", {}).get("CALLBACK_TOKEN") or "").strip()
        if not expected or not constant_time_compare(token, expected):
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.handle_callback(request.data if isinstance(request.data, dict) else {})
        except Exception:  # noqa: BLE001 - always acknowledge; the status query recovers
            logger.exception("M-PESA callback could not be applied")
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
