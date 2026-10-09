from django.urls import path

from . import views

urlpatterns = [
    path("sign/", views.SignUploadView.as_view(), name="media-sign"),
    path("upload/", views.DirectUploadView.as_view(), name="media-upload"),
    path("", views.RegisterUploadView.as_view(), name="media-register"),
    path("<int:pk>/", views.MediaAssetDeleteView.as_view(), name="media-delete"),
]
