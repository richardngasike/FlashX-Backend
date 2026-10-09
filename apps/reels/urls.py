from rest_framework.routers import SimpleRouter

from .views import ReelViewSet

router = SimpleRouter()
router.register("", ReelViewSet, basename="reels")
urlpatterns = router.urls
