from django.db.models import Exists, OuterRef
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.engagement import EngagementMixin
from apps.core.exceptions import ServiceError
from apps.core.pagination import FeedCursorPagination
from apps.core.throttles import ContentCreateThrottle

from . import selectors, services
from .models import Post, PostMedia
from .serializers import PostSerializer, PostWriteSerializer


class PostViewSet(EngagementMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """
    list     GET    /api/posts/?author=&hashtag=&category=&q=
    create   POST   /api/posts/
    retrieve GET    /api/posts/{id}/
    update   PATCH  /api/posts/{id}/
    destroy  DELETE /api/posts/{id}/
    feed     GET    /api/posts/feed/
    """

    lookup_value_regex = r"\d+"
    serializer_class = PostSerializer
    pagination_class = FeedCursorPagination

    def get_throttles(self):
        if self.action == "create":
            return [ContentCreateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = selectors.visible_posts(self.request.user)
        params = self.request.query_params
        if self.action == "list":
            if params.get("author"):
                qs = qs.filter(author_id=params["author"]) if params["author"].isdigit() else qs.none()
            if params.get("username"):
                qs = qs.filter(author__username__iexact=params["username"])
            if params.get("hashtag"):
                qs = qs.filter(hashtags__name=params["hashtag"].lstrip("#").lower())
            if params.get("category"):
                qs = qs.filter(category__slug=params["category"])
            if params.get("media") == "only":
                qs = qs.filter(Exists(PostMedia.objects.filter(post=OuterRef("pk"))))
            if params.get("visibility") in ("public", "followers", "private"):
                qs = qs.filter(visibility=params["visibility"])
            if params.get("q"):
                qs = qs.filter(caption__icontains=params["q"][:100])
            if params.get("tagged"):
                qs = qs.filter(tagged_users__id=params["tagged"]) if params["tagged"].isdigit() else qs.none()
        return selectors.with_relations(qs, self.request.user)

    def _serialize(self, post_id, status_code=status.HTTP_200_OK):
        post = selectors.with_relations(selectors.visible_posts(self.request.user), self.request.user).get(pk=post_id)
        return Response(self.get_serializer(post).data, status=status_code)

    def create(self, request):
        s = PostWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        post = services.create_post(request.user, **s.validated_data)
        return self._serialize(post.pk, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        post = self.get_object()
        self._require_owner(post)
        s = PostWriteSerializer(post, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        services.update_post(post, request.user, s.validated_data)
        return self._serialize(post.pk)

    def update(self, request, pk=None):
        return self.partial_update(request, pk)

    def destroy(self, request, pk=None):
        post = self.get_object()
        self._require_owner(post)
        services.delete_post(post)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _require_owner(self, post):
        if post.author_id != self.request.user.pk:
            raise ServiceError("You can only change your own posts.", code="permission_denied", status_code=403)

    @action(detail=False, methods=["get"])
    def feed(self, request):
        qs = selectors.home_feed(request.user)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


class LikedCursor(FeedCursorPagination):
    ordering = ("-liked_at", "-id")


class LikedPostsView(PostViewSet):
    """GET /api/users/me/likes/ — posts you liked, newest like first (Your Activity)."""

    pagination_class = LikedCursor

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return Post.objects.none()
        from django.db.models import OuterRef, Subquery

        from apps.likes.models import Like

        mine = Like.objects.filter(user=self.request.user, post__isnull=False)
        qs = selectors.visible_posts(self.request.user).filter(pk__in=mine.values("post_id"))
        liked_at = Subquery(mine.filter(post=OuterRef("pk")).values("created_at")[:1])
        return selectors.with_relations(qs, self.request.user).annotate(liked_at=liked_at)
