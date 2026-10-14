from django.urls import path

from . import views

urlpatterns = [
    path("search/", views.MusicSearchView.as_view(), name="music-search"),
    path("genres/", views.GenresView.as_view(), name="music-genres"),
    path("saved/", views.SavedSoundsView.as_view(), name="music-saved"),
    path("mine/", views.MySoundsView.as_view(), name="music-mine"),
    path("recent/", views.RecentSoundsView.as_view(), name="music-recent"),
    path("trending/", views.TrendingSoundsView.as_view(), name="music-trending"),
    path("sounds/", views.SoundCreateView.as_view(), name="music-sound-create"),
    path("sounds/<int:sound_id>/", views.SoundDetailView.as_view(), name="music-sound"),
    path("sounds/<int:sound_id>/save/", views.SoundSaveView.as_view(), name="music-sound-save"),
    path("sounds/<int:sound_id>/report/", views.SoundReportView.as_view(), name="music-sound-report"),
    path("sounds/<int:sound_id>/posts/", views.SoundPostsView.as_view(), name="music-sound-posts"),
    path("sounds/<int:sound_id>/reels/", views.SoundReelsView.as_view(), name="music-sound-reels"),
]
