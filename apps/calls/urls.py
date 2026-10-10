from django.urls import path

from . import views

urlpatterns = [
    path("", views.CallListView.as_view(), name="calls"),
    path("<uuid:pk>/", views.CallDetailView.as_view(), name="calls-detail"),
    path("<uuid:pk>/accept/", views.CallAcceptView.as_view(), name="calls-accept"),
    path("<uuid:pk>/decline/", views.CallDeclineView.as_view(), name="calls-decline"),
    path("<uuid:pk>/cancel/", views.CallCancelView.as_view(), name="calls-cancel"),
    path("<uuid:pk>/end/", views.CallEndView.as_view(), name="calls-end"),
]
