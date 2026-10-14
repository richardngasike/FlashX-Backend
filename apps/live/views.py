from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ServiceError
from apps.core.throttles import ContentCreateThrottle, LiveActionThrottle, MessageSendThrottle, ReactionThrottle
from apps.users.serializers import UserSummarySerializer

from . import livekit, services
from .models import LiveInvite, LiveModerator, LiveStream
from .serializers import (
    LiveCommentSerializer,
    LiveCommentWriteSerializer,
    LiveInviteRespondSerializer,
    LiveInviteSerializer,
    LivePinSerializer,
    LivePollCreateSerializer,
    LiveReactionsSerializer,
    LiveReportSerializer,
    LiveStartSerializer,
    LiveStreamSerializer,
    LiveToggleSerializer,
    LiveUpdateSerializer,
    LiveUserSerializer,
    LiveVoteSerializer,
)


def _room_state(request, stream, after=0) -> dict:
    """
    Everything a live screen needs, polled every few seconds (it doubles as the
    presence heartbeat). Managers (host, moderators) also get the request queue.
    """
    user = request.user
    comments = services.comments_after(stream, user, after)
    invite = services.pending_invite_for(stream, user)
    guest_rows = services.guest_invites(stream)
    mine = next((i for i in guest_rows if i.user_id == user.pk), None)
    moderator_ids = list(LiveModerator.objects.filter(stream=stream).values_list("user_id", flat=True))
    manager = user.pk == stream.host_id or user.pk in moderator_ids
    guests = []
    for inv in guest_rows:
        row = UserSummarySerializer(inv.user).data
        row["muted_by_host"] = inv.muted_by_host
        row["is_moderator"] = inv.user_id in moderator_ids
        guests.append(row)
    my_request = (
        LiveInvite.objects.filter(stream=stream, user=user, status=LiveInvite.Status.REQUESTED).exists()
        if user.pk != stream.host_id
        else False
    )
    pinned_comment = None
    if stream.pinned_comment_id:
        c = stream.pinned_comment
        if c is not None and not c.is_removed:
            pinned_comment = LiveCommentSerializer(c).data
    end_at = stream.ended_at or timezone.now()
    data = {
        "status": stream.status,
        "viewer_count": stream.viewer_count,
        "role": services.role_of(stream, user),
        "invited": invite is not None,
        "requested": my_request,
        "muted_by_host": bool(mine and mine.muted_by_host),
        "is_moderator": user.pk in moderator_ids,
        "guests": guests,
        "comments": LiveCommentSerializer(comments, many=True).data,
        "removed_comment_ids": services.removed_comment_ids(stream),
        "pinned_comment": pinned_comment,
        "pinned_user_id": stream.pinned_user_id,
        "moderator_ids": moderator_ids,
        "poll": services.poll_payload(services.current_poll(stream), user),
        "title": stream.title,
        "description": stream.description,
        "category": stream.category,
        "audio_only": stream.audio_only,
        "allow_requests": stream.allow_requests,
        "is_paused": stream.paused_at is not None,
        "reactions_count": stream.reactions_count,
        "gifts_coins": stream.gifts_coins,
        "duration_seconds": max(0, int((end_at - stream.started_at).total_seconds())),
        "max_guests": services.max_guests(),
    }
    if manager:
        data["requests"] = [UserSummarySerializer(i.user).data for i in services.pending_requests(stream)]
        data["questions"] = LiveCommentSerializer(services.open_questions(stream), many=True).data
    return data


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
        d = s.validated_data
        stream = services.start(
            request.user,
            d["title"],
            description=d["description"],
            category=d["category"],
            visibility=d["visibility"],
            allowed_user_ids=d["allowed_user_ids"],
            audio_only=d["audio_only"],
            allow_requests=d["allow_requests"],
            notify_followers=d["notify_followers"],
        )
        return Response(_session(request, stream, "host"), status=status.HTTP_201_CREATED)


class LiveDetailView(APIView):
    """
    GET   /api/live/{id}/  one stream
    PATCH /api/live/{id}/  host edits title, description, category or allow_requests while live
    """

    serializer_class = LiveStreamSerializer

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        return Response(LiveStreamSerializer(stream, context={"request": request}).data)

    @extend_schema(request=LiveUpdateSerializer)
    def patch(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.update_details(stream, request.user, **s.validated_data)
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
        return Response(_room_state(request, stream, after))

    @extend_schema(request=LiveCommentWriteSerializer)
    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveCommentWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        c = services.comment(stream, request.user, d["text"], reply_to_id=d.get("reply_to_id"), kind=d["kind"])
        return Response(LiveCommentSerializer(c).data, status=status.HTTP_201_CREATED)


class LiveViewersView(APIView):
    """GET /api/live/{id}/viewers/ — who is watching right now (the host picks guests here)."""

    serializer_class = UserSummarySerializer

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        hidden = services.hidden_user_ids(request.user)
        viewers = (
            services.active_viewers(stream)
            .exclude(user_id__in=hidden)
            .select_related("user__profile_image")
            .order_by("-joined_at")[:200]
        )
        users = [v.user for v in viewers]
        data = UserSummarySerializer(users, many=True).data
        if services.can_manage(stream, request.user):
            ids = [u.pk for u in users]
            status_by_user = dict(
                LiveInvite.objects.filter(stream=stream, user_id__in=ids).values_list("user_id", "status")
            )
            mods = set(LiveModerator.objects.filter(stream=stream, user_id__in=ids).values_list("user_id", flat=True))
            for row in data:
                row["live_status"] = status_by_user.get(row["id"])
                row["is_moderator"] = row["id"] in mods
        return Response({"results": data})


class LiveInviteView(APIView):
    """POST /api/live/{id}/invite/ {"user_id": 5} — host (or a moderator) asks a viewer to join on screen."""

    serializer_class = LiveInviteSerializer
    throttle_classes = [LiveActionThrottle]

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
    """POST /api/live/{id}/guests/{user_id}/remove/ — host or moderator removes a guest, or a guest steps down."""

    serializer_class = LiveStreamSerializer

    def post(self, request, pk, user_id):
        stream = services.get_stream(pk, request.user)
        services.remove_guest(stream, request.user, user_id)
        return Response({"ok": True})


class _LiveAction(APIView):
    """Shared base: rate limited, needs the stream visible to you."""

    throttle_classes = [LiveActionThrottle]

    def stream(self, request, pk):
        return services.get_stream(pk, request.user)

    def ok(self, request, stream, **extra):
        stream = services.get_stream(stream.pk, request.user)
        return Response({**_room_state(request, stream), **extra})


class LiveRequestView(_LiveAction):
    """
    POST   /api/live/{id}/requests/  a viewer asks to join on screen
    DELETE /api/live/{id}/requests/  cancel the request
    GET    /api/live/{id}/requests/  host/moderators: the waiting queue, oldest first
    """

    serializer_class = LiveUserSerializer

    def get(self, request, pk):
        stream = self.stream(request, pk)
        if not services.can_manage(stream, request.user):
            raise ServiceError(
                "Only the host or a moderator can see requests.", code="permission_denied", status_code=403
            )
        rows = services.pending_requests(stream)
        return Response({"results": [UserSummarySerializer(i.user).data for i in rows]})

    def post(self, request, pk):
        stream = self.stream(request, pk)
        services.request_to_join(stream, request.user)
        return self.ok(request, stream)

    def delete(self, request, pk):
        stream = self.stream(request, pk)
        services.cancel_request(stream, request.user)
        return self.ok(request, stream)


class LiveRequestRespondView(_LiveAction):
    """POST /api/live/{id}/requests/{user_id}/ {"on": true} accept or {"on": false} reject (host/moderators)."""

    serializer_class = LiveToggleSerializer

    @extend_schema(request=LiveToggleSerializer)
    def post(self, request, pk, user_id):
        stream = self.stream(request, pk)
        s = LiveToggleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.respond_request(stream, request.user, user_id, s.validated_data["on"])
        return self.ok(request, stream)


class LiveMuteGuestView(_LiveAction):
    """POST /api/live/{id}/guests/{user_id}/mute/ {"on": true} — host/moderator mutes (or unmutes) a guest."""

    serializer_class = LiveToggleSerializer

    @extend_schema(request=LiveToggleSerializer)
    def post(self, request, pk, user_id):
        stream = self.stream(request, pk)
        s = LiveToggleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.mute_guest(stream, request.user, user_id, s.validated_data["on"])
        return self.ok(request, stream)


class LivePinView(_LiveAction):
    """
    POST /api/live/{id}/pin/ {"user_id": 7}      host shows a guest large for everyone ({"user_id": null}: the host)
    POST /api/live/{id}/pin/ {"comment_id": 12}  host/moderator pins a comment ({"comment_id": null} unpins)
    """

    serializer_class = LivePinSerializer

    @extend_schema(request=LivePinSerializer)
    def post(self, request, pk):
        stream = self.stream(request, pk)
        s = LivePinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "comment_id" in s.validated_data:
            services.pin_comment(stream, request.user, s.validated_data["comment_id"])
        else:
            services.pin_user(stream, request.user, s.validated_data.get("user_id"))
        return self.ok(request, stream)


class LiveModeratorView(_LiveAction):
    """POST /api/live/{id}/moderators/ {"user_id": 7, "on": true} — host appoints or removes a moderator."""

    serializer_class = LiveUserSerializer

    def post(self, request, pk):
        stream = self.stream(request, pk)
        s = LiveUserSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        on = LiveToggleSerializer(data={"on": request.data.get("on", True)})
        on.is_valid(raise_exception=True)
        services.set_moderator(stream, request.user, s.validated_data["user_id"], on.validated_data["on"])
        return self.ok(request, stream)


class LiveBanView(_LiveAction):
    """
    POST   /api/live/{id}/bans/ {"user_id": 7}  remove someone and keep them out of this live
    DELETE /api/live/{id}/bans/ {"user_id": 7}  let them back in
    """

    serializer_class = LiveUserSerializer

    def post(self, request, pk):
        stream = self.stream(request, pk)
        s = LiveUserSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.ban(stream, request.user, s.validated_data["user_id"])
        return self.ok(request, stream)

    def delete(self, request, pk):
        stream = self.stream(request, pk)
        s = LiveUserSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.unban(stream, request.user, s.validated_data["user_id"])
        return self.ok(request, stream)


class LiveCommentDetailView(_LiveAction):
    """
    DELETE /api/live/{id}/comments/{comment_id}/         author, host or moderator removes a comment
    POST   /api/live/{id}/comments/{comment_id}/answer/  host/moderator marks a question answered
    """

    serializer_class = LiveCommentSerializer

    def delete(self, request, pk, comment_id):
        stream = self.stream(request, pk)
        services.remove_comment(stream, request.user, comment_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LiveAnswerView(_LiveAction):
    serializer_class = LiveToggleSerializer

    def post(self, request, pk, comment_id):
        stream = self.stream(request, pk)
        answered = request.data.get("on", True) if isinstance(request.data, dict) else True
        services.mark_answered(stream, request.user, comment_id, bool(answered))
        return self.ok(request, stream)


class LivePollView(_LiveAction):
    """POST /api/live/{id}/polls/ {"question": "...", "options": ["A", "B"]} — host/moderator starts a poll."""

    serializer_class = LivePollCreateSerializer

    @extend_schema(request=LivePollCreateSerializer)
    def post(self, request, pk):
        stream = self.stream(request, pk)
        s = LivePollCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.create_poll(stream, request.user, s.validated_data["question"], s.validated_data["options"])
        return Response(_room_state(request, services.get_stream(pk, request.user)), status=status.HTTP_201_CREATED)


class LivePollVoteView(_LiveAction):
    """POST /api/live/{id}/polls/{poll_id}/vote/ {"option": 1} — vote (voting again changes it)."""

    serializer_class = LiveVoteSerializer

    def post(self, request, pk, poll_id):
        stream = self.stream(request, pk)
        s = LiveVoteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        poll = services.vote(stream, request.user, poll_id, s.validated_data["option"])
        return Response({"poll": services.poll_payload(poll, request.user)})


class LivePollCloseView(_LiveAction):
    """POST /api/live/{id}/polls/{poll_id}/close/ — host/moderator ends voting."""

    serializer_class = LiveVoteSerializer

    def post(self, request, pk, poll_id):
        stream = self.stream(request, pk)
        poll = services.close_poll(stream, request.user, poll_id)
        return Response({"poll": services.poll_payload(poll, request.user)})


class LiveReactionsView(APIView):
    """POST /api/live/{id}/reactions/ {"count": 5} — batched reaction count for the host's stats."""

    serializer_class = LiveReactionsSerializer
    throttle_classes = [ReactionThrottle]

    def post(self, request, pk):
        stream = services.get_stream(pk, request.user)
        s = LiveReactionsSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response({"counted": services.add_reactions(stream, request.user, s.validated_data["count"])})


class LivePauseView(_LiveAction):
    """POST /api/live/{id}/pause/ {"on": true} — host pauses or resumes."""

    serializer_class = LiveToggleSerializer

    def post(self, request, pk):
        stream = self.stream(request, pk)
        s = LiveToggleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_paused(stream, request.user, s.validated_data["on"])
        return self.ok(request, stream)


class LiveStatsView(APIView):
    """GET /api/live/{id}/stats/ — the host's numbers for a live (during or after)."""

    serializer_class = LiveStreamSerializer

    def get(self, request, pk):
        stream = services.get_stream(pk, request.user)
        return Response(services.stats(stream, request.user))


class LiveReportView(APIView):
    """POST /api/live/{id}/report/ {"reason": "spam", "details": "..."}"""

    serializer_class = LiveReportSerializer

    def get_throttles(self):
        from apps.core.throttles import ReportThrottle

        return [ReportThrottle()]

    def post(self, request, pk):
        from apps.reports.models import Report

        stream = services.get_stream(pk, request.user)
        s = LiveReportSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if s.validated_data["reason"] not in Report.Reason.values:
            raise ServiceError("Choose a reason.", code="invalid_reason")
        services.report(stream, request.user, s.validated_data["reason"], s.validated_data["details"])
        return Response({"reported": True}, status=status.HTTP_201_CREATED)
