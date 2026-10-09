from rest_framework import status
from rest_framework.generics import DestroyAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ServiceError
from apps.core.throttles import UploadThrottle

from . import cloudinary_service as cld
from . import services
from .models import MediaAsset
from .serializers import DirectUploadSerializer, MediaAssetSerializer, RegisterUploadSerializer, SignUploadSerializer


class SignUploadView(APIView):
    """Step 1 of a direct upload: get signed Cloudinary parameters."""

    serializer_class = SignUploadSerializer

    throttle_classes = [UploadThrottle]

    def post(self, request):
        s = SignUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.check_purpose(s.validated_data["purpose"], s.validated_data["resource_type"])
        data = cld.sign_upload(request.user.id, s.validated_data["purpose"], s.validated_data["resource_type"])
        data["max_bytes"] = (
            services._limits()["VIDEO_MAX_BYTES"]
            if s.validated_data["resource_type"] == "video"
            else services._limits()["IMAGE_MAX_BYTES"]
        )
        data["max_duration"] = services.max_duration_for(s.validated_data["purpose"])
        return Response(data)


class RegisterUploadView(APIView):
    """Step 2: register the uploaded file; returns an asset id to attach to content."""

    serializer_class = RegisterUploadSerializer

    def post(self, request):
        s = RegisterUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        asset = services.register_direct_upload(
            request.user,
            purpose=d["purpose"],
            resource_type=d["resource_type"],
            public_id=d["public_id"],
            version=d["version"],
            signature=d["signature"],
            client_meta=d,
        )
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class DirectUploadView(APIView):
    """Alternative: multipart upload through the API (validated, then sent to Cloudinary)."""

    serializer_class = DirectUploadSerializer

    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [UploadThrottle]

    def post(self, request):
        s = DirectUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        asset = services.upload_from_request(
            request.user, purpose=s.validated_data["purpose"], uploaded_file=s.validated_data["file"]
        )
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class MediaAssetDeleteView(DestroyAPIView):
    """Discard an uploaded file that has not been attached to anything yet."""

    serializer_class = MediaAssetSerializer

    def get_queryset(self):
        return MediaAsset.objects.filter(owner=self.request.user)

    def perform_destroy(self, instance):
        if instance.is_attached:
            raise ServiceError("This file is in use. Delete the content instead.", code="media_in_use", status_code=409)
        instance.delete()
