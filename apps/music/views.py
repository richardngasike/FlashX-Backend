from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import FeedCursorPagination, StandardPagination
from apps.posts import selectors as post_selectors
from apps.posts.serializers import PostSerializer
from apps.reels import selectors as reel_selectors
from apps.reels.serializers import ReelSerializer

from . import services


def _seconds(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


class MusicSearchView(APIView):
    """
    GET /api/music/search/?q=&genre=&limit=&min_duration=&max_duration=
    {"catalogue": [Jamendo and Openverse tracks], "original": [FlashX original sounds],
     "popular": [...], "trending": [...], "catalogue_enabled"}.
    Empty q and genre returns this week's popular tracks, trending sounds and the most used sounds on FlashX.
    """

    serializer_class = ReelSerializer

    @extend_schema(operation_id="music_search")
    def get(self, request):
        params = request.query_params
        try:
            limit = max(1, min(int(params.get("limit", 30)), 50))
        except ValueError:
            limit = 30
        return Response(
            services.search(
                request.user,
                params.get("q", ""),
                params.get("genre", ""),
                limit,
                min_seconds=_seconds(params.get("min_duration")),
                max_seconds=_seconds(params.get("max_duration")),
            )
        )


class GenresView(APIView):
    """GET /api/music/genres/ — genre chips for the sound picker."""

    serializer_class = ReelSerializer

    @extend_schema(operation_id="music_genres")
    def get(self, request):
        return Response({"results": [{"id": k, "name": v} for k, v in services.GENRES]})


class SoundCreateSerializer(serializers.Serializer):
    media_id = serializers.IntegerField(min_value=1, help_text="Audio file (or video) uploaded with purpose=sound.")
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    start = serializers.FloatField(required=False, min_value=0, default=0)
    end = serializers.FloatField(required=False, min_value=0, allow_null=True, default=None)
    cover_media_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    is_public = serializers.BooleanField(required=False, default=True)
    allow_reuse = serializers.BooleanField(required=False, default=True)


class SoundUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    allow_reuse = serializers.BooleanField(required=False)
    is_public = serializers.BooleanField(required=False)


class SoundReportSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=20)
    details = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class SoundListMixin:
    """Paged list of sounds as payloads with the viewer's saves."""

    serializer_class = ReelSerializer
    pagination_class = StandardPagination

    def page(self, request, qs):
        paginator = StandardPagination()
        items = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.payloads(items, request.user))


class SoundCreateView(APIView):
    """POST /api/music/sounds/ — publish an original sound from an uploaded or recorded audio file."""

    serializer_class = SoundCreateSerializer

    def get_throttles(self):
        from apps.core.throttles import ContentCreateThrottle

        return [ContentCreateThrottle()]

    def post(self, request):
        s = SoundCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        sound = services.create_uploaded(request.user, **s.validated_data)
        return Response(services.sound_payload(sound, request.user), status=status.HTTP_201_CREATED)


class SoundDetailView(APIView):
    """
    GET /api/music/sounds/{id}/ — a sound's page header.
    PATCH (owner) {title, description, allow_reuse, is_public}; DELETE (owner).
    """

    serializer_class = SoundUpdateSerializer

    @extend_schema(operation_id="music_sound_detail")
    def get(self, request, sound_id):
        return Response(services.sound_payload(services.visible_sound(request.user, sound_id), request.user))

    def patch(self, request, sound_id):
        sound = services.visible_sound(request.user, sound_id)
        s = SoundUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        services.update_sound(sound, request.user, s.validated_data)
        return Response(services.sound_payload(sound, request.user))

    def delete(self, request, sound_id):
        services.remove_sound(services.visible_sound(request.user, sound_id), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SoundSaveView(APIView):
    """POST/DELETE /api/music/sounds/{id}/save/ -> {"is_saved"}"""

    serializer_class = ReelSerializer

    def post(self, request, sound_id):
        services.save_sound(request.user, services.visible_sound(request.user, sound_id))
        return Response({"is_saved": True})

    def delete(self, request, sound_id):
        services.unsave_sound(request.user, services.visible_sound(request.user, sound_id))
        return Response({"is_saved": False})


class SoundReportView(APIView):
    """POST /api/music/sounds/{id}/report/ {"reason", "details"} — e.g. "ip" for audio used without permission."""

    serializer_class = SoundReportSerializer

    def get_throttles(self):
        from apps.core.throttles import ReportThrottle

        return [ReportThrottle()]

    def post(self, request, sound_id):
        from apps.reports.models import Report
        from apps.reports.services import file_report

        sound = services.visible_sound(request.user, sound_id)
        s = SoundReportSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if s.validated_data["reason"] not in Report.Reason.values:
            from apps.core.exceptions import ServiceError

            raise ServiceError("Choose a reason.", code="invalid_reason")
        file_report(
            request.user,
            target_type="sound",
            target_id=sound.pk,
            reason=s.validated_data["reason"],
            details=s.validated_data.get("details", ""),
        )
        return Response({"reported": True}, status=status.HTTP_201_CREATED)


class SavedSoundsView(SoundListMixin, APIView):
    """GET /api/music/saved/ — sounds you saved, newest first."""

    def get(self, request):
        return self.page(request, services.saved_sounds(request.user))


class MySoundsView(SoundListMixin, APIView):
    """GET /api/music/mine/ — your original sounds, including private and switched-off ones."""

    def get(self, request):
        return self.page(request, services.my_sounds(request.user))


class RecentSoundsView(APIView):
    """GET /api/music/recent/ — sounds you used recently on your posts, reels and stories."""

    serializer_class = ReelSerializer

    def get(self, request):
        return Response({"results": services.payloads(services.recent_sounds(request.user), request.user)})


class TrendingSoundsView(APIView):
    """GET /api/music/trending/ — sounds used most on new content this week."""

    serializer_class = ReelSerializer

    def get(self, request):
        return Response({"results": services.payloads(services.trending_sounds(request.user, limit=30), request.user)})


class SoundPostsView(APIView):
    """GET /api/music/sounds/{id}/posts/ — visible posts using this sound, newest first."""

    serializer_class = PostSerializer

    def get(self, request, sound_id):
        sound = services.visible_sound(request.user, sound_id)
        qs = post_selectors.with_relations(post_selectors.visible_posts(request.user).filter(sound=sound), request.user)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PostSerializer(page, many=True, context={"request": request}).data)


class SoundReelsView(APIView):
    """GET /api/music/sounds/{id}/reels/ — visible reels using this sound, newest first."""

    serializer_class = ReelSerializer

    def get(self, request, sound_id):
        sound = services.visible_sound(request.user, sound_id)
        qs = reel_selectors.with_relations(reel_selectors.visible_reels(request.user).filter(sound=sound), request.user)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(ReelSerializer(page, many=True, context={"request": request}).data)
