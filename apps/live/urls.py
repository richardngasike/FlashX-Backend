from django.urls import path

from apps.wallet.views import LiveGiftView

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
    path("<int:pk>/guests/<int:user_id>/mute/", views.LiveMuteGuestView.as_view(), name="live-mute-guest"),
    path("<int:pk>/requests/", views.LiveRequestView.as_view(), name="live-requests"),
    path("<int:pk>/requests/<int:user_id>/", views.LiveRequestRespondView.as_view(), name="live-request-respond"),
    path("<int:pk>/pin/", views.LivePinView.as_view(), name="live-pin"),
    path("<int:pk>/moderators/", views.LiveModeratorView.as_view(), name="live-moderators"),
    path("<int:pk>/bans/", views.LiveBanView.as_view(), name="live-bans"),
    path("<int:pk>/comments/<int:comment_id>/", views.LiveCommentDetailView.as_view(), name="live-comment-detail"),
    path("<int:pk>/comments/<int:comment_id>/answer/", views.LiveAnswerView.as_view(), name="live-comment-answer"),
    path("<int:pk>/polls/", views.LivePollView.as_view(), name="live-polls"),
    path("<int:pk>/polls/<int:poll_id>/vote/", views.LivePollVoteView.as_view(), name="live-poll-vote"),
    path("<int:pk>/polls/<int:poll_id>/close/", views.LivePollCloseView.as_view(), name="live-poll-close"),
    path("<int:pk>/reactions/", views.LiveReactionsView.as_view(), name="live-reactions"),
    path("<int:pk>/gifts/", LiveGiftView.as_view(), name="live-gifts"),
    path("<int:pk>/pause/", views.LivePauseView.as_view(), name="live-pause"),
    path("<int:pk>/stats/", views.LiveStatsView.as_view(), name="live-stats"),
    path("<int:pk>/report/", views.LiveReportView.as_view(), name="live-report"),
]
