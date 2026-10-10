import random

from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Ad
from .serializers import AdSerializer


def pick_ads(count: int) -> list:
    """Weighted random pick of running ads, without repeats."""
    pool = list(Ad.objects.running().only(*[f.name for f in Ad._meta.concrete_fields]))
    chosen = []
    while pool and len(chosen) < count:
        ad = random.choices(pool, weights=[max(1, min(a.weight, 10)) for a in pool], k=1)[0]
        chosen.append(ad)
        pool.remove(ad)
    return chosen


class FeedAdsView(APIView):
    """GET /api/ads/?count=3 — sponsored posts for the feed (the app places them between posts)."""

    serializer_class = AdSerializer

    def get(self, request):
        try:
            count = max(1, min(int(request.query_params.get("count", 3)), 10))
        except ValueError:
            count = 3
        return Response({"results": AdSerializer(pick_ads(count), many=True).data})


class AdImpressionView(APIView):
    """POST /api/ads/{id}/impression/ — the ad was on screen."""

    serializer_class = AdSerializer

    def post(self, request, pk):
        Ad.objects.filter(pk=pk).update(impressions=F("impressions") + 1)
        return Response({"ok": True})


class AdClickView(APIView):
    """POST /api/ads/{id}/click/ — the button was tapped. Returns the link to open."""

    serializer_class = AdSerializer

    def post(self, request, pk):
        ad = get_object_or_404(Ad.objects.running(), pk=pk)
        Ad.objects.filter(pk=pk).update(clicks=F("clicks") + 1)
        return Response({"link_url": ad.link_url})
