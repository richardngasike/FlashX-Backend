from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.users.selectors import base_users
from apps.users.serializers import UserSummarySerializer

from . import services
from .models import Block

User = get_user_model()


class BlockView(APIView):
    """POST blocks the user, DELETE unblocks: /api/users/{id}/block/."""

    serializer_class = UserSummarySerializer

    def post(self, request, pk):
        target = get_object_or_404(User.objects.filter(is_active=True), pk=pk)
        created = services.block(request.user, target)
        return Response({"is_blocked": True}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        services.unblock(request.user, target)
        return Response({"is_blocked": False})


class BlockedUsersView(APIView):
    """GET /api/users/me/blocked/ — accounts you blocked, most recent first."""

    serializer_class = UserSummarySerializer

    def get(self, request):
        mine = Block.objects.filter(blocker=request.user)
        blocked_at = Subquery(mine.filter(blocked=OuterRef("pk")).values("created_at")[:1])
        qs = base_users().filter(pk__in=mine.values("blocked_id")).annotate(blocked_at=blocked_at)
        qs = qs.order_by("-blocked_at", "-id")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = UserSummarySerializer(page, many=True, context={"request": request}).data
        for row, user in zip(data, page, strict=True):
            row["blocked_at"] = user.blocked_at
        return paginator.get_paginated_response(data)
