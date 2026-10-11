from django.urls import path

from . import views

urlpatterns = [
    path("", views.ConversationListView.as_view(), name="messages-conversations"),
    path("conversations/", views.StartConversationView.as_view(), name="messages-start"),
    path("unread-count/", views.UnreadCountView.as_view(), name="messages-unread-count"),
    path("gifs/", views.GifSearchView.as_view(), name="messages-gifs"),
    path("join/<str:code>/", views.JoinGroupView.as_view(), name="messages-join"),
    path("message/bulk-delete/", views.MessageBulkDeleteView.as_view(), name="messages-bulk-delete"),
    path("message/<int:pk>/", views.MessageDeleteView.as_view(), name="messages-delete"),
    path("message/<int:pk>/forward/", views.MessageForwardView.as_view(), name="messages-forward"),
    path("message/<int:pk>/react/", views.MessageReactView.as_view(), name="messages-react"),
    path("message/<int:pk>/pin/", views.MessagePinView.as_view(), name="messages-pin"),
    path("<uuid:conversation_id>/", views.ConversationMessagesView.as_view(), name="messages-thread"),
    path("<uuid:conversation_id>/info/", views.ConversationDetailView.as_view(), name="messages-info"),
    path("<uuid:conversation_id>/read/", views.MarkReadView.as_view(), name="messages-read"),
    path("<uuid:conversation_id>/mute/", views.MuteView.as_view(), name="messages-mute"),
    path("<uuid:conversation_id>/leave/", views.LeaveGroupView.as_view(), name="messages-leave"),
    path("<uuid:conversation_id>/pinned/", views.PinnedMessagesView.as_view(), name="messages-pinned"),
    path("<uuid:conversation_id>/state/", views.ListStateView.as_view(), name="messages-state"),
    path("<uuid:conversation_id>/typing/", views.TypingView.as_view(), name="messages-typing"),
    path("<uuid:conversation_id>/invite/", views.InviteLinkView.as_view(), name="messages-invite"),
    path("<uuid:conversation_id>/members/", views.GroupMembersView.as_view(), name="messages-members"),
    path(
        "<uuid:conversation_id>/members/<int:user_id>/",
        views.GroupMemberDetailView.as_view(),
        name="messages-member-detail",
    ),
]
