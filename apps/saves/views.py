from django.db.models import OuterRef, Subquery
from rest_framework.views import APIView

from apps.core.pagination import FeedCursorPagination
from apps.posts import selectors as post_selectors
from apps.posts.serializers import PostSerializer
from apps.reels import selectors as reel_selectors
from apps.reels.serializers import ReelSerializer

from .models import SavedItem


class SavedCursor(FeedCursorPagination):
    ordering = ("-saved_at", "-id")


class SavedListView(APIView):
    """GET /api/users/me/saved/?type=posts|reels — newest saves first; private to the owner."""

    serializer_class = PostSerializer

    def get(self, request):
        kind = "reel" if request.query_params.get("type") == "reels" else "post"
        saved = SavedItem.objects.filter(user=request.user, **{f"{kind}__isnull": False})
        saved_at = Subquery(saved.filter(**{kind: OuterRef("pk")}).values("created_at")[:1])
        ids = saved.values(f"{kind}_id")
        if kind == "reel":
            qs = reel_selectors.with_relations(
                reel_selectors.visible_reels(request.user).filter(pk__in=ids), request.user
            )
            serializer = ReelSerializer
        else:
            qs = post_selectors.with_relations(
                post_selectors.visible_posts(request.user).filter(pk__in=ids), request.user
            )
            serializer = PostSerializer
        qs = qs.annotate(saved_at=saved_at)
        paginator = SavedCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(serializer(page, many=True, context={"request": request}).data)
