from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.throttles import ContentCreateThrottle, MessageSendThrottle
from apps.users.serializers import UserSummarySerializer

from . import livekit, services
from .models import LiveStream
from .serializers import (
    LiveCommentSerializer,
    LiveCommentWriteSerializer,
    LiveInviteRespondSerializer,
    LiveInviteSerializer,
    LiveStartSerializer,
    LiveStreamSerializer,
)


def _session(request, stream, role):
    stream = services.get_stream(stream.pk, request.user)
    return {
        "stream": LiveStreamSerializer(stream, context={"request": request}).data,
        "role": role,
        "livekit": services.connection(stream, request.user, role),
    }


class LiveListView(APIView):
    """
    GET  /api/live/  streams on air (people you follow first) and whether live is available.
    POST /api/live/  go live. Returns the stream and the LiveKit connection for the host.
    """

    serializer_class = LiveStreamSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [ContentCreateThrottle()]
        return super().get_throttles()

    @extend_schema(operation_id="live_list")
    def get(self, request):
        streams = services.live_now(request.user)[:30]
        return Response(
            {
                "available": livekit.is_configured(),
                "results": LiveStreamSerializer(streams, many=True, context={"request": request}).data,
            }
        )

    @extend_schema(request=LiveStartSerializer)
    def post(self, request):
        s = LiveStartSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        stream = services.start(request.user, s.validated_data["title"])
        return Response(_session(request, stream, "host"), status=status.HTTP_201_CREATED)


class LiveDetailView(APIView):
    serializer_class = LiveStreamSerializer

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        return Response(LiveStreamSerializer(stream, context={"request": request}).data)


class LiveJoinView(APIView):
    """POST /api/live/{id}/join/ — start watching. Returns the LiveKit connection."""

    serializer_class = LiveStreamSerializer

    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        role = services.join(stream, request.user)
        return Response(_session(request, stream, role))


class LiveLeaveView(APIView):
    serializer_class = LiveStreamSerializer

    def post(self, request, pk):
        stream = get_object_or_404(LiveStream, pk=pk)
        services.leave(stream, request.user)
        return Response({"ok": True})


class LiveEndView(APIView):
    serializer_class = LiveStreamSerializer

    def post(self, request, pk):
        stream = get_object_or_404(LiveStream, pk=pk)
        stream = services.end(stream, request.user)
        return Response(LiveStreamSerializer(stream, context={"request": request}).data)


class LiveCommentsView(APIView):
    """
    GET  /api/live/{id}/comments/?after=<id>  new comments plus live state. Polled every
         few seconds; it doubles as the presence heartbeat.
    POST /api/live/{id}/comments/  {"text": "..."}
    """

    serializer_class = LiveCommentSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [MessageSendThrottle()]
        return super().get_throttles()

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        if stream.status == LiveStream.Status.LIVE:
            services.heartbeat(stream, request.user)
        try:
            after = max(0, int(request.query_params.get("after", 0)))
        except ValueError:
            after = 0
        comments = services.comments_after(stream, request.user, after)
        invite = services.pending_invite_for(stream, request.user)
        return Response(
            {
                "status": stream.status,
                "viewer_count": stream.viewer_count,
                "role": services.role_of(stream, request.user),
                "invited": invite is not None,
                "guests": UserSummarySerializer(services.guests(stream), many=True).data,
                "comments": LiveCommentSerializer(comments, many=True).data,
            }
        )

    @extend_schema(request=LiveCommentWriteSerializer)
    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveCommentWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        c = services.comment(stream, request.user, s.validated_data["text"])
        return Response(LiveCommentSerializer(c).data, status=status.HTTP_201_CREATED)


class LiveViewersView(APIView):
    """GET /api/live/{id}/viewers/ — who is watching right now (the host picks guests here)."""

    serializer_class = UserSummarySerializer

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        viewers = services.active_viewers(stream).select_related("user__profile_image").order_by("-joined_at")[:200]
        return Response({"results": UserSummarySerializer([v.user for v in viewers], many=True).data})


class LiveInviteView(APIView):
    """POST /api/live/{id}/invite/ {"user_id": 5} — host asks a viewer to join on screen."""

    serializer_class = LiveInviteSerializer

    @extend_schema(request=LiveInviteSerializer)
    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveInviteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.invite(stream, request.user, s.validated_data["user_id"])
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


class LiveInviteRespondView(APIView):
    """POST /api/live/{id}/invite/respond/ {"accept": true} — returns a new connection with publish rights."""

    serializer_class = LiveInviteRespondSerializer

    @extend_schema(request=LiveInviteRespondSerializer)
    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveInviteRespondSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        role = services.respond_invite(stream, request.user, s.validated_data["accept"])
        return Response(_session(request, stream, role))


class LiveRemoveGuestView(APIView):
    """POST /api/live/{id}/guests/{user_id}/remove/ — host removes a guest, or a guest steps down."""

    serializer_class = LiveStreamSerializer

    def post(self, request, pk, user_id):
        stream = services.get_stream(pk, request.user)
        services.remove_guest(stream, request.user, user_id)
        return Response({"ok": True})
