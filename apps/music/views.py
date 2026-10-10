from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import FeedCursorPagination
from apps.posts import selectors as post_selectors
from apps.posts.serializers import PostSerializer
from apps.reels import selectors as reel_selectors
from apps.reels.serializers import ReelSerializer

from . import services
from .models import Sound


class MusicSearchView(APIView):
    """
    GET /api/music/search/?q=&genre=&limit=
    {"catalogue": [Jamendo tracks], "original": [FlashX original sounds], "popular": [...], "catalogue_enabled"}.
    Empty q and genre returns this week's popular tracks plus the most used sounds on FlashX.
    """

    serializer_class = ReelSerializer

    @extend_schema(operation_id="music_search")
    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 30)), 50))
        except ValueError:
            limit = 30
        return Response(
            services.search(
                request.user,
                request.query_params.get("q", ""),
                request.query_params.get("genre", ""),
                limit,
            )
        )


class GenresView(APIView):
    """GET /api/music/genres/ — genre chips for the sound picker."""

    serializer_class = ReelSerializer

    @extend_schema(operation_id="music_genres")
    def get(self, request):
        return Response({"results": [{"id": k, "name": v} for k, v in services.GENRES]})


def _sound(request, sound_id):
    sound = get_object_or_404(Sound.objects.select_related("owner"), pk=sound_id)
    if sound.owner_id and sound.owner_id != request.user.pk:
        from apps.blocks.selectors import is_blocked_between

        if not sound.owner.is_active or is_blocked_between(request.user, sound.owner):
            from django.http import Http404

            raise Http404
    return sound


class SoundDetailView(APIView):
    """GET /api/music/sounds/{id}/ — a sound's page header (title, artist, licence, how many posts use it)."""

    serializer_class = ReelSerializer

    @extend_schema(operation_id="music_sound_detail")
    def get(self, request, sound_id):
        return Response(services.sound_payload(_sound(request, sound_id)))


class SoundPostsView(APIView):
    """GET /api/music/sounds/{id}/posts/ — visible posts using this sound, newest first."""

    serializer_class = PostSerializer

    def get(self, request, sound_id):
        sound = _sound(request, sound_id)
        qs = post_selectors.with_relations(post_selectors.visible_posts(request.user).filter(sound=sound), request.user)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PostSerializer(page, many=True, context={"request": request}).data)


class SoundReelsView(APIView):
    """GET /api/music/sounds/{id}/reels/ — visible reels using this sound, newest first."""

    serializer_class = ReelSerializer

    def get(self, request, sound_id):
        sound = _sound(request, sound_id)
        qs = reel_selectors.with_relations(reel_selectors.visible_reels(request.user).filter(sound=sound), request.user)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(ReelSerializer(page, many=True, context={"request": request}).data)
