from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import ChatCursorPagination, FeedCursorPagination
from apps.core.throttles import GifSearchThrottle, MessageSendThrottle, ReactionThrottle, TypingThrottle
from apps.posts.selectors import visible_posts

from . import selectors, services
from .models import ConversationParticipant, Message, MessageReaction
from .serializers import (
    BulkDeleteSerializer,
    ConversationSerializer,
    EditMessageSerializer,
    ForwardSerializer,
    GroupMembersSerializer,
    GroupUpdateSerializer,
    ListStateSerializer,
    MemberRoleSerializer,
    MessageSerializer,
    MuteSerializer,
    PinSerializer,
    ReactSerializer,
    SendMessageSerializer,
    StartConversationSerializer,
    TypingSerializer,
)


def _conversation_payload(request, conversations):
    ids = [c.last_message_id for c in conversations if getattr(c, "last_message_id", None)]
    last = {
        m.pk: m for m in Message.objects.filter(pk__in=ids).select_related("sender").prefetch_related("attachments")
    }
    ctx = {"request": request, "last_messages": last}
    return ConversationSerializer(conversations, many=True, context=ctx).data


def _message_context(request, conversation_id, messages):
    others = (
        ConversationParticipant.objects.filter(conversation_id=conversation_id)
        .exclude(user=request.user)
        .aggregate(read=Max("last_read_at"), delivered=Max("delivered_at"))
    )
    post_ids = {m.shared_post_id for m in messages if m.shared_post_id}
    visible = (
        set(visible_posts(request.user).filter(pk__in=post_ids).values_list("pk", flat=True)) if post_ids else set()
    )
    return {
        "request": request,
        "others_read_until": others["read"],
        "others_delivered_until": others["delivered"],
        "visible_post_ids": visible,
    }


def _message_payload(request, message, status_code=status.HTTP_200_OK):
    msg = selectors.messages_for(request.user, message.conversation_id).get(pk=message.pk)
    return Response(
        MessageSerializer(msg, context=_message_context(request, message.conversation_id, [msg])).data,
        status=status_code,
    )


class ConversationListView(APIView):
    """
    GET  /api/messages/          conversations (?q= filters by name, ?archived=1 lists archived chats).
                                 The first page starts with your pinned chats.
    POST /api/messages/          send a message (to conversation_id or recipient_id) with
                                 text, media_ids (photos, videos, audio, documents), gif_url,
                                 reply_to_id and client_id (makes retries safe)
    """

    serializer_class = SendMessageSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [MessageSendThrottle()]
        return super().get_throttles()

    @extend_schema(operation_id="messages_conversations_list")
    def get(self, request):
        archived = request.query_params.get("archived") in ("1", "true")
        qs = selectors.visible_conversations(request.user, archived=archived)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(memberships__user__username__icontains=q)
                | Q(memberships__user__full_name__icontains=q)
            ).distinct()
        pinned = []
        if not archived and not q:
            # Pinned chats lead the first page and are left out of the paged rest.
            if not request.query_params.get("cursor"):
                pinned = list(qs.filter(my_pinned__isnull=False).order_by("-my_pinned"))
            qs = qs.filter(my_pinned__isnull=True)
        paginator = ConversationCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = pinned + list(page)
        services.mark_delivered(request.user, [c.pk for c in rows])
        response = paginator.get_paginated_response(_conversation_payload(request, rows))
        if not archived and not request.query_params.get("cursor"):
            response.data["archived_count"] = selectors.visible_conversations(request.user, archived=True).count()
        return response

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
            media_ids=d.get("media_ids"),
            gif_url=d.get("gif_url", ""),
            reply_to_id=d.get("reply_to_id"),
            client_id=d.get("client_id", ""),
        )
        services.set_attachment_names(msg, d.get("attachment_names"))
        services.set_typing(request.user, convo.pk, False)
        return _message_payload(request, msg, status.HTTP_201_CREATED)


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
    """
    GET    /api/messages/{conversation_id}/       newest first (?q= searches this chat's messages)
    DELETE /api/messages/{conversation_id}/       clears it for you
    """

    serializer_class = MessageSerializer

    @extend_schema(operation_id="messages_thread_list")
    def get(self, request, conversation_id):
        m = services.membership(request.user, conversation_id)
        qs = selectors.messages_for(request.user, conversation_id, m.cleared_at)
        q = (request.query_params.get("q") or "").strip()[:100]
        if q:
            qs = qs.filter(
                Q(content__icontains=q) | Q(attachments__name__icontains=q),
                kind=Message.Kind.USER,
                is_deleted=False,
            ).distinct()
        else:
            services.mark_delivered(request.user, [conversation_id])
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
    PATCH  /api/messages/message/{id}/               sender edits the text (within 15 minutes): {"content": "..."}
    DELETE /api/messages/message/{id}/?for=everyone  sender only: "Message deleted" for all members (default)
    DELETE /api/messages/message/{id}/?for=me        hide it from your own view only
    """

    serializer_class = MessageSerializer

    @extend_schema(request=EditMessageSerializer)
    def patch(self, request, pk):
        s = EditMessageSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        msg = services.edit_message(request.user, pk, s.validated_data["content"])
        return _message_payload(request, msg)

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
        admins = set(
            ConversationParticipant.objects.filter(conversation=convo, is_admin=True).values_list("user_id", flat=True)
        ) | {convo.created_by_id}
        users.sort(key=lambda u: (u.pk != convo.created_by_id, u.pk not in admins, u.pk != request.user.pk))
        data = UserListSerializer(users, many=True, context={"request": request}).data
        for row in data:
            row["is_admin"] = row["id"] in admins
            row["is_creator"] = row["id"] == convo.created_by_id
        return Response({"results": data, "admin_id": convo.created_by_id, "is_group": convo.is_group})

    def post(self, request, conversation_id):
        s = GroupMembersSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        added = services.add_group_members(request.user, conversation_id, s.validated_data["user_ids"])
        return Response({"added": added}, status=status.HTTP_201_CREATED)


class GroupMemberDetailView(APIView):
    """
    PATCH  /api/messages/{id}/members/{user_id}/  admin makes someone an admin or not: {"is_admin": true}
    DELETE /api/messages/{id}/members/{user_id}/  admin removes someone (or you remove yourself)
    """

    serializer_class = GroupMembersSerializer

    @extend_schema(request=MemberRoleSerializer)
    def patch(self, request, conversation_id, user_id):
        s = MemberRoleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_admin(request.user, conversation_id, user_id, s.validated_data["is_admin"])
        return Response({"user_id": user_id, "is_admin": s.validated_data["is_admin"]})

    def delete(self, request, conversation_id, user_id):
        services.remove_group_member(request.user, conversation_id, user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageBulkDeleteView(APIView):
    """POST /api/messages/message/bulk-delete/ {"ids": [..], "scope": "me" | "everyone"} — multi-select delete."""

    serializer_class = BulkDeleteSerializer

    def post(self, request):
        s = BulkDeleteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        ids = s.validated_data["ids"]
        msgs = list(
            Message.objects.filter(pk__in=ids, conversation__memberships__user=request.user)
            .select_related("conversation")
            .distinct()
        )
        everyone = s.validated_data["scope"] == "everyone"
        if everyone and any(m.sender_id != request.user.pk or m.kind == Message.Kind.SYSTEM for m in msgs):
            from apps.core.exceptions import ServiceError

            raise ServiceError(
                "You can only delete your own messages for everyone.", code="permission_denied", status_code=403
            )
        for m in msgs:
            if everyone:
                services.delete_message(request.user, m)
            else:
                services.delete_message_for_me(request.user, m)
        return Response({"deleted": [m.pk for m in msgs]})


class MessageForwardView(APIView):
    """POST /api/messages/message/{id}/forward/ {"conversation_ids": [..], "recipient_ids": [..]}"""

    serializer_class = ForwardSerializer

    def get_throttles(self):
        return [MessageSendThrottle()]

    def post(self, request, pk):
        s = ForwardSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        sent = services.forward_message(
            request.user,
            pk,
            conversation_ids=s.validated_data.get("conversation_ids", []),
            recipient_ids=s.validated_data.get("recipient_ids", []),
        )
        return Response(
            {"forwarded": [{"id": m.pk, "conversation_id": str(m.conversation_id)} for m in sent]},
            status=status.HTTP_201_CREATED,
        )


class MessageReactView(APIView):
    """
    GET    /api/messages/message/{id}/react/  who reacted, with which emoji
    POST   /api/messages/message/{id}/react/  {"emoji": "..."} (reacting again changes it)
    DELETE /api/messages/message/{id}/react/  remove your reaction
    """

    serializer_class = ReactSerializer

    def get_throttles(self):
        if self.request.method in ("POST", "DELETE"):
            return [ReactionThrottle()]
        return super().get_throttles()

    def get(self, request, pk):
        from apps.blocks.selectors import hidden_user_ids
        from apps.users.serializers import UserSummarySerializer

        msg = services._message_for(request.user, pk)
        hidden = hidden_user_ids(request.user)
        rows = (
            MessageReaction.objects.filter(message=msg)
            .exclude(user_id__in=hidden)
            .select_related("user__profile_image")
            .order_by("-created_at")[:200]
        )
        return Response(
            {
                "results": [
                    {"emoji": r.emoji, "user": UserSummarySerializer(r.user, context={"request": request}).data}
                    for r in rows
                ]
            }
        )

    def post(self, request, pk):
        s = ReactSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        msg = services.react(request.user, pk, s.validated_data["emoji"])
        return _message_payload(request, msg)

    def delete(self, request, pk):
        msg = services.unreact(request.user, pk)
        return _message_payload(request, msg)


class MessagePinView(APIView):
    """POST /api/messages/message/{id}/pin/ {"pinned": true|false} — admins in groups, either person in direct chats."""

    serializer_class = PinSerializer

    def post(self, request, pk):
        s = PinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        msg = services.pin_message(request.user, pk, s.validated_data["pinned"])
        return _message_payload(request, msg)


class PinnedMessagesView(APIView):
    """GET /api/messages/{id}/pinned/ — pinned messages, newest pin first."""

    serializer_class = MessageSerializer

    def get(self, request, conversation_id):
        m = services.membership(request.user, conversation_id)
        qs = selectors.messages_for(request.user, conversation_id, m.cleared_at).filter(
            pinned_at__isnull=False, is_deleted=False
        )
        msgs = list(qs.order_by("-pinned_at")[:20])
        ctx = _message_context(request, conversation_id, msgs)
        return Response({"results": MessageSerializer(msgs, many=True, context=ctx).data})


class ListStateView(APIView):
    """POST /api/messages/{id}/state/ {"pinned"?: bool, "archived"?: bool, "unread"?: bool} — your chat list only."""

    serializer_class = ListStateSerializer

    def post(self, request, conversation_id):
        s = ListStateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_list_state(request.user, conversation_id, **s.validated_data)
        convo = selectors.conversations_for(request.user).get(pk=conversation_id)
        return Response(_conversation_payload(request, [convo])[0])


class TypingView(APIView):
    """
    GET  /api/messages/{id}/typing/  who is typing now
    POST /api/messages/{id}/typing/  {"typing": true} while typing (refresh every few seconds), false to stop
    """

    serializer_class = TypingSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [TypingThrottle()]
        return super().get_throttles()

    def get(self, request, conversation_id):
        users = services.typing_users(request.user, conversation_id)
        return Response({"users": [{"id": u.pk, "username": u.username, "name": services._display(u)} for u in users]})

    def post(self, request, conversation_id):
        s = TypingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_typing(request.user, conversation_id, s.validated_data["typing"])
        return Response({"typing": s.validated_data["typing"]})


class InviteLinkView(APIView):
    """
    GET    /api/messages/{id}/invite/  the group's invite code (created on first use; admins only)
    POST   /api/messages/{id}/invite/  reset it (old links stop working)
    DELETE /api/messages/{id}/invite/  turn invite links off
    """

    serializer_class = MuteSerializer

    def get(self, request, conversation_id):
        return Response({"code": services.invite_link(request.user, conversation_id)})

    def post(self, request, conversation_id):
        return Response({"code": services.invite_link(request.user, conversation_id, reset=True)})

    def delete(self, request, conversation_id):
        services.invite_link(request.user, conversation_id, disable=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class JoinGroupView(APIView):
    """
    GET  /api/messages/join/{code}/  preview the group behind an invite link
    POST /api/messages/join/{code}/  join it
    """

    serializer_class = ConversationSerializer

    def get(self, request, code):
        from apps.users.serializers import avatar_payload

        convo = services.group_for_code(code)
        return Response(
            {
                "id": str(convo.pk),
                "title": convo.title or "Group chat",
                "avatar": avatar_payload(convo.image) if convo.image_id else None,
                "member_count": ConversationParticipant.objects.filter(conversation=convo).count(),
                "is_member": ConversationParticipant.objects.filter(conversation=convo, user=request.user).exists(),
            }
        )

    def post(self, request, code):
        convo = services.join_with_code(request.user, code)
        convo = selectors.conversations_for(request.user).get(pk=convo.pk)
        return Response(_conversation_payload(request, [convo])[0], status=status.HTTP_201_CREATED)


class GifSearchView(APIView):
    """GET /api/messages/gifs/?q=&type=gifs|stickers&offset= — trending when q is empty."""

    serializer_class = MuteSerializer
    throttle_classes = [GifSearchThrottle]

    def get(self, request):
        p = request.query_params
        try:
            offset = int(p.get("offset") or 0)
        except ValueError:
            offset = 0
        return Response(services.gif_search(p.get("q", ""), kind=p.get("type", "gifs"), offset=offset))
