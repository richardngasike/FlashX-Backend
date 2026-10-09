from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.cron import MaintenanceCronView
from apps.core.views import HealthView
from apps.posts.views import LikedPostsView
from apps.saves.views import SavedListView
from apps.search.urls import explore_urlpatterns, search_urlpatterns

admin.site.site_header = "FlashX Administration"
admin.site.site_title = "FlashX Admin"
admin.site.index_title = "Platform management"

api = [
    path("health/", HealthView.as_view(), name="health"),
    path("cron/maintenance/", MaintenanceCronView.as_view(), name="cron-maintenance"),
    path("auth/", include("apps.users.auth_urls")),
    path("users/me/saved/", SavedListView.as_view(), name="users-me-saved"),
    path("users/me/likes/", LikedPostsView.as_view({"get": "list"}), name="users-me-likes"),
    path("users/", include("apps.users.urls")),
    path("media/", include("apps.media.urls")),
    path("posts/", include("apps.posts.urls")),
    path("comments/", include("apps.comments.urls")),
    path("reels/", include("apps.reels.urls")),
    path("stories/", include("apps.stories.urls")),
    path("messages/", include("apps.messaging.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("search/", include(search_urlpatterns)),
    path("explore/", include(explore_urlpatterns)),
    path("reports/", include("apps.reports.urls")),
]

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", include(api)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
]
