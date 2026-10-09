from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.engagement import EngagementMixin
from apps.core.exceptions import ServiceError
from apps.core.pagination import FeedCursorPagination
from apps.core.throttles import ContentCreateThrottle
from apps.follows.models import Follow

from . import selectors, services
from .serializers import ReelCreateSerializer, ReelSerializer, ReelUpdateSerializer, ReelViewSerializer


class ReelViewSet(EngagementMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """
    GET /api/reels/?feed=following|for_you&author=&hashtag=
    The client should request small pages (page_size=5) and prefetch the next
    page near the end; only the current and next videos need initialising.
    """

    lookup_value_regex = r"\d+"
    serializer_class = ReelSerializer
    pagination_class = FeedCursorPagination

    def get_throttles(self):
        if self.action == "create":
            return [ContentCreateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = selectors.visible_reels(self.request.user)
        params = self.request.query_params
        if self.action == "list":
            if params.get("feed") == "following":
                qs = qs.filter(author_id__in=Follow.objects.filter(follower=self.request.user).values("following_id"))
            if params.get("author"):
                qs = qs.filter(author_id=params["author"]) if params["author"].isdigit() else qs.none()
            if params.get("hashtag"):
                qs = qs.filter(hashtags__name=params["hashtag"].lstrip("#").lower())
        return selectors.with_relations(qs, self.request.user)

    def _serialize(self, reel_id, code=status.HTTP_200_OK):
        reel = selectors.with_relations(selectors.visible_reels(self.request.user), self.request.user).get(pk=reel_id)
        return Response(self.get_serializer(reel).data, status=code)

    def _require_owner(self, reel):
        if reel.author_id != self.request.user.pk:
            raise ServiceError("You can only change your own reels.", code="permission_denied", status_code=403)

    def create(self, request):
        s = ReelCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        reel = services.create_reel(request.user, **s.validated_data)
        return self._serialize(reel.pk, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        reel = self.get_object()
        self._require_owner(reel)
        s = ReelUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        services.update_reel(reel, s.validated_data)
        return self._serialize(reel.pk)

    def update(self, request, pk=None):
        return self.partial_update(request, pk)

    def destroy(self, request, pk=None):
        reel = self.get_object()
        self._require_owner(reel)
        services.delete_reel(reel)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def view(self, request, pk=None):
        reel = self.get_object()
        s = ReelViewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        views = services.record_view(reel, request.user, s.validated_data["watched_seconds"])
        return Response({"views": views})
