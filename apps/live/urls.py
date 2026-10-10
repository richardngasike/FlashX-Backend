from django.urls import path

from . import views

urlpatterns = [
    path("", views.LiveListView.as_view(), name="live-list"),
    path("<int:pk>/", views.LiveDetailView.as_view(), name="live-detail"),
    path("<int:pk>/join/", views.LiveJoinView.as_view(), name="live-join"),
    path("<int:pk>/leave/", views.LiveLeaveView.as_view(), name="live-leave"),
    path("<int:pk>/end/", views.LiveEndView.as_view(), name="live-end"),
    path("<int:pk>/comments/", views.LiveCommentsView.as_view(), name="live-comments"),
    path("<int:pk>/viewers/", views.LiveViewersView.as_view(), name="live-viewers"),
    path("<int:pk>/invite/", views.LiveInviteView.as_view(), name="live-invite"),
    path("<int:pk>/invite/respond/", views.LiveInviteRespondView.as_view(), name="live-invite-respond"),
    path("<int:pk>/guests/<int:user_id>/remove/", views.LiveRemoveGuestView.as_view(), name="live-remove-guest"),
]
