from datetime import datetime
from datetime import timezone as dt_timezone

from django.db.models import Count, DateTimeField, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce

from apps.users.selectors import base_users

from .models import Conversation, ConversationParticipant, Message

EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def conversations_for(user):
    """Conversations the user is in, annotated with unread counts and the last message id."""
    mine = ConversationParticipant.objects.filter(user=user, conversation=OuterRef("pk"))
    last_read = Subquery(mine.values("last_read_at")[:1])
    cleared = Subquery(mine.values("cleared_at")[:1])
    last_msg = (
        Message.objects.filter(conversation=OuterRef("pk"))
        .exclude(hidden_for__user=user)
        .order_by("-created_at", "-id")
    )
    return (
        Conversation.objects.filter(memberships__user=user)
        .select_related("image")
        .annotate(
            my_last_read=Coalesce(last_read, Value(EPOCH), output_field=DateTimeField()),
            my_cleared=Coalesce(cleared, Value(EPOCH), output_field=DateTimeField()),
            is_muted=Subquery(mine.values("is_muted")[:1]),
            last_message_id=Subquery(last_msg.values("id")[:1]),
        )
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__created_at__gt=F("my_last_read"), messages__is_deleted=False)
                & ~Q(messages__sender=user),
                distinct=True,
            )
        )
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=ConversationParticipant.objects.select_related("user__profile_image"),
            )
        )
    )


def visible_conversations(user):
    """
    The chat list. Groups always show (they get a "created the group" event, so
    ``last_message_at`` is set from the start); a direct thread shows once it
    has a message. Threads the user cleared stay hidden until something new arrives.
    """
    return (
        conversations_for(user)
        .filter(Q(last_message_at__isnull=False) | Q(is_group=True))
        .annotate(activity_at=Coalesce("last_message_at", "created_at"))
        .filter(activity_at__gt=F("my_cleared"))
        .order_by("-activity_at", "-created_at")
    )


def messages_for(user, conversation_id, cleared_at=None):
    qs = (
        Message.objects.filter(conversation_id=conversation_id)
        .select_related(
            "sender__profile_image", "reply_to__sender", "shared_post__author", "shared_reel__author", "story__author"
        )
        .prefetch_related("shared_post__media")
    )
    if cleared_at:
        qs = qs.filter(created_at__gt=cleared_at)
    return qs.exclude(hidden_for__user=user)


def users_by_id(ids):
    return {u.pk: u for u in base_users().filter(pk__in=ids)}
