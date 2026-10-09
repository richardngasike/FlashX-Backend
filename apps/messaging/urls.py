from django.urls import path

from . import views

urlpatterns = [
    path("", views.ConversationListView.as_view(), name="messages-conversations"),
    path("conversations/", views.StartConversationView.as_view(), name="messages-start"),
    path("unread-count/", views.UnreadCountView.as_view(), name="messages-unread-count"),
    path("message/<int:pk>/", views.MessageDeleteView.as_view(), name="messages-delete"),
    path("<uuid:conversation_id>/", views.ConversationMessagesView.as_view(), name="messages-thread"),
    path("<uuid:conversation_id>/info/", views.ConversationDetailView.as_view(), name="messages-info"),
    path("<uuid:conversation_id>/read/", views.MarkReadView.as_view(), name="messages-read"),
    path("<uuid:conversation_id>/mute/", views.MuteView.as_view(), name="messages-mute"),
]
