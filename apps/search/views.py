from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import SearchPagination
from apps.posts.models import Category, Hashtag
from apps.posts.serializers import CategorySerializer, HashtagSerializer, PostSerializer
from apps.reels.serializers import ReelSerializer
from apps.users.selectors import suggested_users
from apps.users.serializers import UserListSerializer

from . import services
from .models import RecentSearch
from .serializers import RecentSearchSerializer


def _categories_payload():
    from apps.posts.serializers import category_cover_fallbacks

    cats = list(Category.objects.filter(is_active=True))
    return CategorySerializer(cats, many=True, context={"category_fallbacks": category_cover_fallbacks(cats)}).data


TYPES = {
    "users": (services.search_users, UserListSerializer),
    "hashtags": (lambda viewer, q: services.search_hashtags(q), HashtagSerializer),
    "posts": (services.search_posts, PostSerializer),
    "reels": (services.search_reels, ReelSerializer),
}


class SearchView(APIView):
    """
    GET /api/search/?q=nairobi&type=all|users|hashtags|posts|reels&limit=&offset=
    ``all`` returns a short preview of each type; a specific type is paginated.
    """

    serializer_class = UserListSerializer

    def get(self, request):
        q = services.clean(request.query_params.get("q"))
        kind = request.query_params.get("type", "all")
        if not q:
            return Response({"query": q, "results": []} if kind != "all" else {"query": q})
        ctx = {"request": request}
        if kind == "all":
            out = {"query": q}
            for name, (fn, serializer) in TYPES.items():
                out[name] = serializer(fn(request.user, q)[:5], many=True, context=ctx).data
            return Response(out)
        if kind not in TYPES:
            raise ValidationError({"type": "Use one of: all, users, hashtags, posts, reels."})
        fn, serializer = TYPES[kind]
        paginator = SearchPagination()
        page = paginator.paginate_queryset(fn(request.user, q), request, view=self)
        return paginator.get_paginated_response(serializer(page, many=True, context=ctx).data)


class RecentSearchView(APIView):
    serializer_class = RecentSearchSerializer

    def get(self, request):
        items = RecentSearch.objects.filter(user=request.user)[:20]
        return Response({"results": RecentSearchSerializer(items, many=True).data})

    def post(self, request):
        s = RecentSearchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj, _ = RecentSearch.objects.update_or_create(
            user=request.user, kind=s.validated_data["kind"], value=s.validated_data["value"]
        )
        stale = RecentSearch.objects.filter(user=request.user).values_list("id", flat=True)[50:]
        RecentSearch.objects.filter(id__in=list(stale)).delete()
        return Response(RecentSearchSerializer(obj).data, status=status.HTTP_201_CREATED)

    @extend_schema(operation_id="search_recent_clear")
    def delete(self, request):
        RecentSearch.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecentSearchItemView(APIView):
    serializer_class = RecentSearchSerializer

    @extend_schema(operation_id="search_recent_delete")
    def delete(self, request, pk):
        RecentSearch.objects.filter(user=request.user, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExploreView(APIView):
    """GET /api/explore/?tab=for_you|trending|following&category=travel — media posts grid."""

    serializer_class = PostSerializer

    def get(self, request):
        tab = request.query_params.get("tab", "for_you")
        if tab not in ("for_you", "trending", "following"):
            tab = "for_you"
        qs = services.explore_posts(request.user, tab, request.query_params.get("category"))
        paginator = SearchPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = PostSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)


class ExploreOverviewView(APIView):
    """Everything the Discover screen needs above the grid, in one call."""

    serializer_class = PostSerializer

    def get(self, request):
        ctx = {"request": request}
        return Response(
            {
                "categories": _categories_payload(),
                "trending_hashtags": HashtagSerializer(services.trending_hashtags(), many=True).data,
                "suggested_users": UserListSerializer(
                    suggested_users(request.user, limit=8), many=True, context=ctx
                ).data,
                "trending_reels": ReelSerializer(services.trending_reels(request.user), many=True, context=ctx).data,
            }
        )


class CategoryListView(APIView):
    serializer_class = CategorySerializer

    def get(self, request):
        return Response({"results": _categories_payload()})


class HashtagDetailView(APIView):
    serializer_class = HashtagSerializer

    def get(self, request, name):
        tag = get_object_or_404(Hashtag, name=name.lstrip("#").lower())
        return Response(HashtagSerializer(tag).data)
