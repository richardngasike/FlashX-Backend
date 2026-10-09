from django.urls import path

from . import views

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notifications-list"),
    path("unread-count/", views.UnreadCountView.as_view(), name="notifications-unread-count"),
    path("devices/", views.DeviceTokenView.as_view(), name="notifications-devices"),
    path("read-all/", views.MarkAllReadView.as_view(), name="notifications-read-all"),
    path("<int:pk>/read/", views.MarkReadView.as_view(), name="notifications-read"),
    path("<int:pk>/", views.NotificationDeleteView.as_view(), name="notifications-delete"),
]
