from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.pagination import FeedCursorPagination
from apps.likes import services as like_services

from . import selectors, services
from .serializers import CommentSerializer


class OldestFirstCursor(FeedCursorPagination):
    ordering = ("created_at", "id")
    page_size = 20


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
        if comment.reel_id and not visible_reels().filter(pk=comment.reel_id).exists():
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
                request.user, target, s.validated_data["content"], s.validated_data.get("parent_id")
            )
            comment = selectors.visible_comments(request.user).get(pk=comment.pk)
            return Response(
                CommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED
            )

        kind = target._meta.model_name
        qs = selectors.visible_comments(request.user).filter(parent_comment__isnull=True, **{kind: target})
        paginator = OldestFirstCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(CommentSerializer(page, many=True, context={"request": request}).data)
