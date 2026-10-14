from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.pagination import FeedCursorPagination
from apps.likes import services as like_services

from . import selectors, services
from .serializers import CommentSerializer, CommentUpdateSerializer


class OldestFirstCursor(FeedCursorPagination):
    ordering = ("created_at", "id")
    page_size = 20


class NewestFirstCursor(FeedCursorPagination):
    ordering = ("-created_at", "-id")
    page_size = 20


class TopCommentsPagination(LimitOffsetPagination):
    """Most liked first. Offsets rather than a cursor: like counts move."""

    default_limit = 20
    max_limit = 50


class CommentViewSet(GenericViewSet):
    """/api/comments/{id}/ — delete, like, replies."""

    lookup_value_regex = r"\d+"
    serializer_class = CommentSerializer

    def get_queryset(self):
        return selectors.visible_comments(self.request.user)

    def _visible_target_check(self, comment):
        from apps.posts.selectors import visible_posts
        from apps.reels.selectors import visible_reels

        if comment.post_id and not visible_posts(self.request.user).filter(pk=comment.post_id).exists():
            raise NotFound()
        if comment.reel_id and not visible_reels(self.request.user).filter(pk=comment.reel_id).exists():
            raise NotFound()

    def get_object(self):
        comment = super().get_object()
        self._visible_target_check(comment)
        return comment

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def destroy(self, request, pk=None):
        comment = self.get_object()
        services.delete_comment(request.user, comment)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def partial_update(self, request, pk=None):
        """PATCH {"content"}: the author edits their comment."""
        comment = self.get_object()
        s = CommentUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.edit_comment(request.user, comment, s.validated_data["content"])
        return Response(self.get_serializer(self.get_queryset().get(pk=comment.pk)).data)

    @action(detail=True, methods=["post", "delete"])
    def pin(self, request, pk=None):
        """POST pins (replacing any pinned comment), DELETE unpins. Post or reel author only."""
        comment = self.get_object()
        services.set_pinned(request.user, comment, request.method == "POST")
        return Response(self.get_serializer(self.get_queryset().get(pk=comment.pk)).data)

    @action(detail=True, methods=["post", "delete"])
    def like(self, request, pk=None):
        comment = self.get_object()
        if request.method == "POST":
            count = like_services.like(request.user, comment)
            return Response({"is_liked": True, "likes_count": count})
        count = like_services.unlike(request.user, comment)
        return Response({"is_liked": False, "likes_count": count})

    @action(detail=True, methods=["get"], pagination_class=OldestFirstCursor)
    def replies(self, request, pk=None):
        parent = self.get_object()
        qs = self.get_queryset().filter(parent_comment=parent)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


class CommentListMixin:
    """Used by Post/Reel viewsets: GET/POST {target}/{id}/comments/."""

    def _comments_response(self, request, target):
        from .serializers import CommentCreateSerializer

        if request.method == "POST":
            s = CommentCreateSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            comment = services.create_comment(
                request.user,
                target,
                s.validated_data["content"],
                s.validated_data.get("parent_id"),
                gif_url=s.validated_data.get("gif_url", ""),
                gif_width=s.validated_data.get("gif_width"),
                gif_height=s.validated_data.get("gif_height"),
            )
            comment = selectors.visible_comments(request.user).get(pk=comment.pk)
            return Response(
                CommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED
            )

        # ?sort=oldest (default) | newest | top. The pinned comment leads the first page.
        kind = target._meta.model_name
        qs = selectors.visible_comments(request.user).filter(parent_comment__isnull=True, **{kind: target})
        sort = request.query_params.get("sort", "oldest")
        if sort == "top":
            paginator = TopCommentsPagination()
            ordered = qs.exclude(is_pinned=True).order_by("-likes_count", "-replies_count", "created_at", "id")
        else:
            paginator = NewestFirstCursor() if sort == "newest" else OldestFirstCursor()
            ordered = qs.exclude(is_pinned=True)
        first_page = not any(k in request.query_params for k in ("cursor", "offset"))
        page = list(paginator.paginate_queryset(ordered, request, view=self))
        if first_page:
            page = list(qs.filter(is_pinned=True)[:1]) + page
        return paginator.get_paginated_response(CommentSerializer(page, many=True, context={"request": request}).data)
