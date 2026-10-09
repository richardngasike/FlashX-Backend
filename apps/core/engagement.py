"""Like / save / comment / share endpoints shared by posts and reels."""

from django.db.models import F
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.comments.views import CommentListMixin
from apps.core.pagination import FeedCursorPagination
from apps.core.throttles import ContentCreateThrottle, MessageSendThrottle


class EngagementMixin(CommentListMixin):
    @action(detail=True, methods=["post", "delete"])
    def like(self, request, pk=None):
        from apps.likes import services

        target = self.get_object()
        if request.method == "POST":
            return Response({"is_liked": True, "likes_count": services.like(request.user, target)})
        return Response({"is_liked": False, "likes_count": services.unlike(request.user, target)})

    @action(detail=True, methods=["get"], url_path="likes")
    def likers(self, request, pk=None):
        from apps.likes.models import Like
        from apps.users.selectors import base_users, with_follow_flags
        from apps.users.serializers import UserListSerializer

        target = self.get_object()
        user_ids = Like.objects.filter(**{target._meta.model_name: target}).values("user_id")
        qs = with_follow_flags(base_users(request.user).filter(pk__in=user_ids), request.user).order_by(
            "-created_at", "-id"
        )
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(UserListSerializer(page, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post", "delete"])
    def save(self, request, pk=None):
        from apps.saves import services

        target = self.get_object()
        if request.method == "POST":
            return Response({"is_saved": True, "saves_count": services.save(request.user, target)})
        return Response({"is_saved": False, "saves_count": services.unsave(request.user, target)})

    @action(detail=True, methods=["get", "post"], throttle_classes=[ContentCreateThrottle])
    def comments(self, request, pk=None):
        return self._comments_response(request, self.get_object())

    @action(detail=True, methods=["post"], throttle_classes=[ContentCreateThrottle])
    def comment(self, request, pk=None):
        """Alias of POST comments/ (matches the spec's /api/posts/{id}/comment/)."""
        return self._comments_response(request, self.get_object())

    @action(detail=True, methods=["post"], throttle_classes=[MessageSendThrottle])
    def share(self, request, pk=None):
        from apps.messaging import services as messaging
        from apps.posts.serializers import ShareSerializer

        target = self.get_object()
        s = ShareSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        kind = target._meta.model_name
        sent = messaging.share_content(
            request.user, s.validated_data["recipient_ids"], text=s.validated_data.get("message", ""), **{kind: target}
        )
        type(target).objects.filter(pk=target.pk).update(shares_count=F("shares_count") + len(sent))
        target.refresh_from_db(fields=["shares_count"])
        return Response(
            {"shared_with": len(sent), "conversation_ids": [str(c) for c in sent], "shares_count": target.shares_count},
            status=status.HTTP_201_CREATED,
        )
