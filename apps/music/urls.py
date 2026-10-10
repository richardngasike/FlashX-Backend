from django.urls import path

from . import views

urlpatterns = [
    path("search/", views.MusicSearchView.as_view(), name="music-search"),
    path("genres/", views.GenresView.as_view(), name="music-genres"),
    path("sounds/<int:sound_id>/", views.SoundDetailView.as_view(), name="music-sound"),
    path("sounds/<int:sound_id>/posts/", views.SoundPostsView.as_view(), name="music-sound-posts"),
    path("sounds/<int:sound_id>/reels/", views.SoundReelsView.as_view(), name="music-sound-reels"),
]
