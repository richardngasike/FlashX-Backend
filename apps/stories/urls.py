from rest_framework.routers import SimpleRouter

from .views import StoryViewSet

router = SimpleRouter()
router.register("", StoryViewSet, basename="stories")
urlpatterns = router.urls
