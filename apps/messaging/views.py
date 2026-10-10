from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import ChatCursorPagination, FeedCursorPagination
from apps.core.throttles import MessageSendThrottle
from apps.posts.selectors import visible_posts

from . import selectors, services
from .models import ConversationParticipant, Message
from .serializers import (
    ConversationSerializer,
    GroupMembersSerializer,
    GroupUpdateSerializer,
    MessageSerializer,
    MuteSerializer,
    SendMessageSerializer,
    StartConversationSerializer,
)


def _conversation_payload(request, conversations):
    ids = [c.last_message_id for c in conversations if getattr(c, "last_message_id", None)]
    last = {m.pk: m for m in Message.objects.filter(pk__in=ids).select_related("sender")}
    ctx = {"request": request, "last_messages": last}
    return ConversationSerializer(conversations, many=True, context=ctx).data


def _message_context(request, conversation_id, messages):
    others_read = (
        ConversationParticipant.objects.filter(conversation_id=conversation_id)
        .exclude(user=request.user)
        .aggregate(m=Max("last_read_at"))["m"]
    )
    post_ids = {m.shared_post_id for m in messages if m.shared_post_id}
    visible = (
        set(visible_posts(request.user).filter(pk__in=post_ids).values_list("pk", flat=True)) if post_ids else set()
    )
    return {"request": request, "others_read_until": others_read, "visible_post_ids": visible}


class ConversationListView(APIView):
    """
    GET  /api/messages/          conversations (?q= filters by name)
    POST /api/messages/          send a message (to conversation_id or recipient_id)
    """

    serializer_class = SendMessageSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [MessageSendThrottle()]
        return super().get_throttles()

    @extend_schema(operation_id="messages_conversations_list")
    def get(self, request):
        qs = selectors.visible_conversations(request.user)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(memberships__user__username__icontains=q)
                | Q(memberships__user__full_name__icontains=q)
            ).distinct()
        paginator = ConversationCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(_conversation_payload(request, page))

    def post(self, request):
        s = SendMessageSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("recipient_id"):
            other = services._active_user(d["recipient_id"])
            convo = services.get_or_create_direct(request.user, other)
        else:
            convo = services.membership(request.user, d["conversation_id"]).conversation
        msg = services.send_message(
            request.user,
            convo,
            content=d.get("content", ""),
            media_id=d.get("media_id"),
            reply_to_id=d.get("reply_to_id"),
        )
        msg = selectors.messages_for(request.user, convo.pk).get(pk=msg.pk)
        return Response(
            MessageSerializer(msg, context=_message_context(request, convo.pk, [msg])).data,
            status=status.HTTP_201_CREATED,
        )


class ConversationCursor(FeedCursorPagination):
    ordering = ("-activity_at", "-created_at")
    page_size = 25


class StartConversationView(APIView):
    serializer_class = StartConversationSerializer

    def post(self, request):
        s = StartConversationSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("recipient_id"):
            convo = services.get_or_create_direct(request.user, services._active_user(d["recipient_id"]))
        else:
            convo = services.create_group(request.user, d["participant_ids"], d.get("title", ""))
        convo = selectors.conversations_for(request.user).get(pk=convo.pk)
        return Response(_conversation_payload(request, [convo])[0], status=status.HTTP_201_CREATED)


class ConversationMessagesView(APIView):
    """GET /api/messages/{conversation_id}/  newest first. DELETE clears it for you."""

    serializer_class = MessageSerializer

    @extend_schema(operation_id="messages_thread_list")
    def get(self, request, conversation_id):
        m = services.membership(request.user, conversation_id)
        qs = selectors.messages_for(request.user, conversation_id, m.cleared_at)
        paginator = ChatCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        ctx = _message_context(request, conversation_id, page)
        return paginator.get_paginated_response(MessageSerializer(page, many=True, context=ctx).data)

    def delete(self, request, conversation_id):
        services.clear_conversation(request.user, conversation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationDetailView(APIView):
    """
    GET   /api/messages/{id}/info/  one conversation
    PATCH /api/messages/{id}/info/  group admin: {"title": "...", "image_id": 12} or {"remove_image": true}
    """

    serializer_class = ConversationSerializer

    def get(self, request, conversation_id):
        convo = get_object_or_404(selectors.conversations_for(request.user), pk=conversation_id)
        return Response(_conversation_payload(request, [convo])[0])

    @extend_schema(request=GroupUpdateSerializer)
    def patch(self, request, conversation_id):
        s = GroupUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.update_group(request.user, conversation_id, **s.validated_data)
        convo = selectors.conversations_for(request.user).get(pk=conversation_id)
        return Response(_conversation_payload(request, [convo])[0])


class MarkReadView(APIView):
    serializer_class = MuteSerializer

    def post(self, request, conversation_id):
        updated = services.mark_read(request.user, conversation_id)
        return Response({"marked_read": updated})


class MuteView(APIView):
    serializer_class = MuteSerializer

    def post(self, request, conversation_id):
        s = MuteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_muted(request.user, conversation_id, s.validated_data["muted"])
        return Response({"is_muted": s.validated_data["muted"]})


class MessageDeleteView(APIView):
    """
    DELETE /api/messages/message/{id}/?for=everyone  sender only: "Message deleted" for all members (default)
    DELETE /api/messages/message/{id}/?for=me        hide it from your own view only
    """

    serializer_class = MessageSerializer

    def delete(self, request, pk):
        msg = get_object_or_404(
            Message.objects.filter(conversation__memberships__user=request.user)
            .select_related("conversation")
            .distinct(),
            pk=pk,
        )
        body = request.data if isinstance(request.data, dict) else {}
        scope = request.query_params.get("for") or body.get("for") or "everyone"
        if scope == "me":
            services.delete_message_for_me(request.user, msg)
        else:
            services.delete_message(request.user, msg)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UnreadCountView(APIView):
    serializer_class = MuteSerializer

    def get(self, request):
        return Response({"unread_conversations": services.unread_conversations_count(request.user)})


class LeaveGroupView(APIView):
    """POST /api/messages/{id}/leave/ — leave a group conversation."""

    serializer_class = ConversationSerializer

    def post(self, request, conversation_id):
        services.leave_group(request.user, conversation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupMembersView(APIView):
    """
    GET  /api/messages/{id}/members/  every member (you included), admin first
    POST /api/messages/{id}/members/  admin adds people: {"user_ids": [..]}
    """

    serializer_class = GroupMembersSerializer

    def get(self, request, conversation_id):
        m = services.membership(request.user, conversation_id)
        convo = m.conversation
        from apps.users.selectors import with_follow_flags
        from apps.users.serializers import UserListSerializer

        member_ids = ConversationParticipant.objects.filter(conversation=convo).values("user_id")
        users = list(with_follow_flags(selectors.base_users().filter(pk__in=member_ids), request.user))
        users.sort(key=lambda u: (u.pk != convo.created_by_id, u.pk != request.user.pk, u.full_name.lower()))
        data = UserListSerializer(users, many=True, context={"request": request}).data
        for row in data:
            row["is_admin"] = row["id"] == convo.created_by_id
        return Response({"results": data, "admin_id": convo.created_by_id, "is_group": convo.is_group})

    def post(self, request, conversation_id):
        s = GroupMembersSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        added = services.add_group_members(request.user, conversation_id, s.validated_data["user_ids"])
        return Response({"added": added}, status=status.HTTP_201_CREATED)


class GroupMemberDetailView(APIView):
    """DELETE /api/messages/{id}/members/{user_id}/ — admin removes someone (or you remove yourself)."""

    serializer_class = GroupMembersSerializer

    def delete(self, request, conversation_id, user_id):
        services.remove_group_member(request.user, conversation_id, user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
