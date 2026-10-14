from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.throttles import ReactionThrottle

from . import services


class GiftSendSerializer(serializers.Serializer):
    gift = serializers.SlugField(max_length=30, help_text="Gift slug from the catalogue.")
    quantity = serializers.IntegerField(min_value=1, default=1)
    recipient_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    client_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class RechargeSerializer(serializers.Serializer):
    method = serializers.CharField(max_length=20)
    coins = serializers.IntegerField(min_value=1)


class TransferSerializer(serializers.Serializer):
    recipient_id = serializers.IntegerField(min_value=1)
    coins = serializers.IntegerField(min_value=1)


class WalletView(APIView):
    """GET /api/wallet/ — balance (and its Ksh value), received and sent totals, the last few transactions."""

    serializer_class = GiftSendSerializer

    def get(self, request):
        return Response(services.summary(request.user))


class TransactionsView(APIView):
    """GET /api/wallet/transactions/?type=all|gifts|gifts_sent|gifts_received|recharge|bonus|transfers"""

    serializer_class = GiftSendSerializer

    def get(self, request):
        services.get_wallet(request.user)
        paginator = StandardPagination()
        qs = services.transactions(request.user, request.query_params.get("type", "all"))
        items = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.transaction_payload(t) for t in items])


class GiftHistoryView(APIView):
    """GET /api/wallet/gift-history/?direction=received|sent — gifts with who sent or got them."""

    serializer_class = GiftSendSerializer

    def get(self, request):
        direction = "sent" if request.query_params.get("direction") == "sent" else "received"
        paginator = StandardPagination()
        items = paginator.paginate_queryset(services.gift_history(request.user, direction), request, view=self)
        out = []
        for g in items:
            other = g.recipient if direction == "sent" else g.sender
            out.append(
                {
                    "id": g.pk,
                    "gift": services.gift_ref(g.gift, g.quantity),
                    "coins": g.coins,
                    "direction": direction,
                    "user": {"id": other.pk, "username": other.username},
                    "stream_id": g.stream_id,
                    "created_at": g.created_at.isoformat(),
                }
            )
        return paginator.get_paginated_response(out)


class GiftCatalogView(APIView):
    """GET /api/wallet/gifts/ — gifts that can be sent in live videos, cheapest first within each category."""

    serializer_class = GiftSendSerializer

    def get(self, request):
        return Response({"results": [services.gift_payload(g) for g in services.catalog()]})


class RechargeOptionsView(APIView):
    """GET /api/wallet/recharge/ — payment methods and coin packages (buying is not open yet)."""

    serializer_class = RechargeSerializer

    def get(self, request):
        return Response(services.recharge_options())

    def post(self, request):
        """Buying coins isn't open yet: always answers 503 coming_soon."""
        RechargeSerializer(data=request.data).is_valid(raise_exception=True)
        raise services.coming_soon("Payments")


class TransferView(APIView):
    """POST /api/wallet/transfer/ — sending coins to people isn't open yet (503 coming_soon)."""

    serializer_class = TransferSerializer

    def post(self, request):
        TransferSerializer(data=request.data).is_valid(raise_exception=True)
        raise services.coming_soon("Coin transfers")


class LiveGiftView(APIView):
    """
    POST /api/live/{id}/gifts/ {gift, quantity, recipient_id?, client_id}
    Sends a gift to the host (default) or a guest on screen. Returns the gift
    and the new balance; 402 insufficient_coins with details {balance, needed}.
    """

    serializer_class = GiftSendSerializer

    def get_throttles(self):
        return [ReactionThrottle()]

    def post(self, request, pk):
        from apps.live import services as live

        stream = live.get_stream(pk, request.user)
        s = GiftSendSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        send, balance = services.send_live_gift(
            stream,
            request.user,
            gift_slug=d["gift"],
            quantity=d["quantity"],
            recipient_id=d.get("recipient_id"),
            client_id=d.get("client_id", ""),
        )
        return Response(
            {
                "id": send.pk,
                "gift": services.gift_ref(send.gift, send.quantity),
                "coins": send.coins,
                "recipient_id": send.recipient_id,
                "balance": balance,
            },
            status=status.HTTP_201_CREATED,
        )
