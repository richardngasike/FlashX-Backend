from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination

from . import services
from .serializers import CallSerializer, EndCallSerializer, StartCallSerializer


def _payload(request, call, with_connection=False):
    data = {"call": CallSerializer(call, context={"request": request}).data}
    if with_connection and call.status in ("ringing", "accepted"):
        data["livekit"] = services.connection(call, request.user)
    return data


class CallListView(APIView):
    """
    GET  /api/calls/  your call history, newest first
    POST /api/calls/  {"user_id": 7, "kind": "voice" | "video"} — ring someone you follow mutually.
         Returns the call and the LiveKit connection; status is "busy" if they are on another call.
    """

    serializer_class = CallSerializer

    @extend_schema(operation_id="calls_history")
    def get(self, request):
        paginator = StandardPagination()
        page = paginator.paginate_queryset(services.history(request.user), request, view=self)
        return paginator.get_paginated_response(CallSerializer(page, many=True, context={"request": request}).data)

    @extend_schema(request=StartCallSerializer)
    def post(self, request):
        s = StartCallSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        call = services.start(request.user, s.validated_data["user_id"], s.validated_data["kind"])
        return Response(_payload(request, call, with_connection=True), status=status.HTTP_201_CREATED)


class CallDetailView(APIView):
    """GET /api/calls/{id}/ — current state (the app polls this while a call is ringing or live)."""

    serializer_class = CallSerializer

    def get(self, request, pk):
        return Response(_payload(request, services.get_call(request.user, pk)))


class CallAcceptView(APIView):
    serializer_class = CallSerializer

    def post(self, request, pk):
        call = services.accept(request.user, pk)
        return Response(_payload(request, call, with_connection=True))


class CallDeclineView(APIView):
    serializer_class = CallSerializer

    def post(self, request, pk):
        return Response(_payload(request, services.decline(request.user, pk)))


class CallCancelView(APIView):
    serializer_class = CallSerializer

    def post(self, request, pk):
        return Response(_payload(request, services.cancel(request.user, pk)))


class CallEndView(APIView):
    serializer_class = EndCallSerializer

    @extend_schema(request=EndCallSerializer)
    def post(self, request, pk):
        s = EndCallSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(_payload(request, services.end(request.user, pk, failed=s.validated_data["failed"])))
