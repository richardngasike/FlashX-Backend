from django.urls import path

from . import views

urlpatterns = [
    path("", views.MyGifsView.as_view(), name="gifs-mine"),
    path("discover/", views.PublicGifsView.as_view(), name="gifs-discover"),
    path("upload/", views.GifUploadView.as_view(), name="gifs-upload"),
    path("from-video/", views.GifFromVideoView.as_view(), name="gifs-from-video"),
    path("from-images/", views.GifFromImagesView.as_view(), name="gifs-from-images"),
    path("<int:gif_id>/", views.GifDetailView.as_view(), name="gifs-detail"),
]
