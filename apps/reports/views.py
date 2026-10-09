from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.throttles import ReportThrottle

from . import services
from .models import Report
from .serializers import ReasonSerializer, ReportCreateSerializer, ReportSerializer


class ReportView(APIView):
    """POST /api/reports/  {target_type, target_id, reason, details}. GET lists your own reports."""

    serializer_class = ReportCreateSerializer

    def get_throttles(self):
        return [ReportThrottle()] if self.request.method == "POST" else super().get_throttles()

    def get(self, request):
        reports = Report.objects.filter(reporter=request.user)[:50]
        return Response({"results": ReportSerializer(reports, many=True).data})

    def post(self, request):
        s = ReportCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        report = services.file_report(request.user, **s.validated_data)
        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)


class ReportReasonsView(APIView):
    serializer_class = ReasonSerializer

    def get(self, request):
        return Response({"results": [{"value": v, "label": label} for v, label in Report.Reason.choices]})
