from django.urls import path

from . import views

urlpatterns = [
    path("", views.FeedAdsView.as_view(), name="ads-feed"),
    path("<int:pk>/impression/", views.AdImpressionView.as_view(), name="ads-impression"),
    path("<int:pk>/click/", views.AdClickView.as_view(), name="ads-click"),
]
