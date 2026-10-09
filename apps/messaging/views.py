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
    MessageSerializer,
    MuteSerializer,
    SendMessageSerializer,
    StartConversationSerializer,
)


def _conversation_payload(request, conversations):
    ids = [c.last_message_id for c in conversations if getattr(c, "last_message_id", None)]
    last = {m.pk: m for m in Message.objects.filter(pk__in=ids)}
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
    ordering = ("-last_message_at", "-created_at")
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
    serializer_class = ConversationSerializer

    def get(self, request, conversation_id):
        convo = get_object_or_404(selectors.conversations_for(request.user), pk=conversation_id)
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
    serializer_class = MessageSerializer

    def delete(self, request, pk):
        msg = get_object_or_404(Message.objects.filter(conversation__memberships__user=request.user).distinct(), pk=pk)
        services.delete_message(request.user, msg)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UnreadCountView(APIView):
    serializer_class = MuteSerializer

    def get(self, request):
        return Response({"unread_conversations": services.unread_conversations_count(request.user)})
