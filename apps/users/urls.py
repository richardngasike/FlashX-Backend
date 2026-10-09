from django.urls import path

from apps.blocks.views import BlockView

from . import views

urlpatterns = [
    path("me/", views.MeView.as_view(), name="users-me"),
    path("suggested/", views.SuggestedUsersView.as_view(), name="users-suggested"),
    path("by-username/<str:username>/", views.UserByUsernameView.as_view(), name="users-by-username"),
    path("<int:pk>/", views.UserDetailView.as_view(), name="users-detail"),
    path("<int:pk>/follow/", views.FollowView.as_view(), name="users-follow"),
    path("<int:pk>/block/", BlockView.as_view(), name="users-block"),
    path("<int:pk>/remove-follower/", views.RemoveFollowerView.as_view(), name="users-remove-follower"),
    path("<int:pk>/followers/", views.FollowersView.as_view(), name="users-followers"),
    path("<int:pk>/following/", views.FollowingView.as_view(), name="users-following"),
]
