from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.throttles import ContentCreateThrottle

from . import services


class GifUploadSerializer(serializers.Serializer):
    media_id = serializers.IntegerField(min_value=1, help_text="A .gif uploaded with purpose=gif.")
    title = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    is_public = serializers.BooleanField(required=False, default=False)


class GifFromVideoSerializer(GifUploadSerializer):
    media_id = serializers.IntegerField(min_value=1, help_text="A video uploaded with purpose=gif.")
    start = serializers.FloatField(min_value=0)
    end = serializers.FloatField(min_value=0)
    width = serializers.IntegerField(required=False, default=320)
    fps = serializers.IntegerField(required=False, default=10)
    speed = serializers.FloatField(required=False, default=1.0)
    text = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")


class GifFromImagesSerializer(serializers.Serializer):
    media_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=2, max_length=60)
    delay_ms = serializers.IntegerField(required=False, default=500)
    width = serializers.IntegerField(required=False, default=320)
    aspect = serializers.ChoiceField(choices=["square", "portrait", "landscape"], required=False, default="square")
    loop = serializers.BooleanField(required=False, default=True)
    text = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    title = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    is_public = serializers.BooleanField(required=False, default=False)


class GifUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=80, required=False, allow_blank=True)
    is_public = serializers.BooleanField(required=False)


def _page(view, request, qs):
    paginator = StandardPagination()
    items = paginator.paginate_queryset(qs.select_related("owner"), request, view=view)
    return paginator.get_paginated_response([services.payload(g, request.user) for g in items])


class MyGifsView(APIView):
    """GET /api/gifs/?q= — your GIF library, newest first."""

    serializer_class = GifUpdateSerializer

    def get(self, request):
        return _page(self, request, services.my_gifs(request.user, request.query_params.get("q", "").strip()[:80]))


class PublicGifsView(APIView):
    """GET /api/gifs/discover/?q= — GIFs other people shared publicly, most used first."""

    serializer_class = GifUpdateSerializer

    def get(self, request):
        return _page(self, request, services.public_gifs(request.user, request.query_params.get("q", "").strip()[:80]))


class _CreateView(APIView):
    def get_throttles(self):
        return [ContentCreateThrottle()]

    def respond(self, gif, request):
        return Response(services.payload(gif, request.user), status=status.HTTP_201_CREATED)


class GifUploadView(_CreateView):
    """POST /api/gifs/upload/ {media_id, title, is_public} — add a GIF file you have."""

    serializer_class = GifUploadSerializer

    def post(self, request):
        s = GifUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self.respond(services.create_from_upload(request.user, **s.validated_data), request)


class GifFromVideoView(_CreateView):
    """POST /api/gifs/from-video/ {media_id, start, end, width, fps, speed, text, title, is_public}"""

    serializer_class = GifFromVideoSerializer

    def post(self, request):
        s = GifFromVideoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self.respond(services.create_from_video(request.user, **s.validated_data), request)


class GifFromImagesView(_CreateView):
    """POST /api/gifs/from-images/ {media_ids (in order), delay_ms, width, aspect, loop, text, title, is_public}"""

    serializer_class = GifFromImagesSerializer

    def post(self, request):
        s = GifFromImagesSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return self.respond(services.create_from_images(request.user, **s.validated_data), request)


class GifDetailView(APIView):
    """GET a GIF (yours or a public one); PATCH {title, is_public} and DELETE for the owner."""

    serializer_class = GifUpdateSerializer

    def get(self, request, gif_id):
        return Response(services.payload(services.visible_gif(request.user, gif_id), request.user))

    def patch(self, request, gif_id):
        gif = services.visible_gif(request.user, gif_id)
        s = GifUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        services.update_gif(gif, request.user, s.validated_data)
        return Response(services.payload(gif, request.user))

    def delete(self, request, gif_id):
        services.delete_gif(services.visible_gif(request.user, gif_id), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
