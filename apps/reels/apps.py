from django.apps import AppConfig


class ReelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reels"
    label = "reels"

    def ready(self):
        from apps.media.signals import connect_asset_cleanup

        from .models import Reel

        connect_asset_cleanup(Reel)
