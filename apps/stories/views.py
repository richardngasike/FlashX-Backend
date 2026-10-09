from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.exceptions import ServiceError
from apps.core.pagination import FeedCursorPagination
from apps.core.throttles import ContentCreateThrottle, MessageSendThrottle
from apps.users.selectors import base_users, with_follow_flags
from apps.users.serializers import UserListSerializer

from . import selectors, services
from .models import Story, StoryReaction, StoryView
from .serializers import (
    StoryCreateSerializer,
    StoryReactSerializer,
    StoryReplySerializer,
    StorySerializer,
    StoryTraySerializer,
)


class StoryViewSet(GenericViewSet):
    lookup_value_regex = r"\d+"
    serializer_class = StorySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return Story.objects.none()
        return selectors.active_stories(self.request.user)

    def get_throttles(self):
        if self.action == "create":
            return [ContentCreateThrottle()]
        if self.action == "reply":
            return [MessageSendThrottle()]
        return super().get_throttles()

    def list(self, request):
        """Story tray: groups of active stories by author."""
        groups = selectors.tray(request.user)
        return Response({"results": StoryTraySerializer(groups, many=True, context={"request": request}).data})

    def create(self, request):
        s = StoryCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        story = services.create_story(request.user, **s.validated_data)
        story = self.get_queryset().get(pk=story.pk)
        return Response(self.get_serializer(story).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def destroy(self, request, pk=None):
        story = self.get_object()
        if story.author_id != request.user.pk:
            raise ServiceError("You can only delete your own stories.", code="permission_denied", status_code=403)
        services.delete_story(story)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path=r"user/(?P<user_id>\d+)")
    def user(self, request, user_id=None):
        author = get_object_or_404(base_users(request.user), pk=user_id)
        stories = self.get_queryset().filter(author=author).order_by("created_at", "id")
        return Response({"results": self.get_serializer(stories, many=True).data})

    @action(detail=True, methods=["post"])
    def view(self, request, pk=None):
        story = self.get_object()
        services.mark_viewed(story, request.user)
        return Response({"is_seen": True})

    @action(detail=True, methods=["post", "delete"])
    def react(self, request, pk=None):
        story = self.get_object()
        if request.method == "DELETE":
            services.unreact(story, request.user)
            return Response({"my_reaction": None})
        s = StoryReactSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.react(story, request.user, s.validated_data["reaction"])
        return Response({"my_reaction": s.validated_data["reaction"]})

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        story = self.get_object()
        s = StoryReplySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        message = services.reply(story, request.user, s.validated_data["content"])
        return Response(
            {"conversation_id": str(message.conversation_id), "message_id": message.pk}, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def viewers(self, request, pk=None):
        story = self.get_object()
        if story.author_id != request.user.pk:
            raise ServiceError("Only the author can see who viewed a story.", code="permission_denied", status_code=403)
        viewer_ids = StoryView.objects.filter(story=story).values("viewer_id")
        reactions = dict(StoryReaction.objects.filter(story=story).values_list("user_id", "reaction"))
        viewed_at = StoryView.objects.filter(story=story, viewer=OuterRef("pk")).values("created_at")[:1]
        qs = with_follow_flags(base_users(request.user).filter(pk__in=viewer_ids), request.user).annotate(
            viewed_at=Subquery(viewed_at)
        )
        paginator = ViewersCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = UserListSerializer(page, many=True, context={"request": request}).data
        for row in data:
            row["reaction"] = reactions.get(row["id"])
        for row, user in zip(data, page, strict=False):
            row["viewed_at"] = user.viewed_at
        return paginator.get_paginated_response(data)


class ViewersCursor(FeedCursorPagination):
    ordering = ("-viewed_at", "-id")
    page_size = 30
