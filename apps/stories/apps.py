from django.apps import AppConfig


class StoriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.stories"
    label = "stories"

    def ready(self):
        from apps.media.signals import connect_asset_cleanup

        from .models import Story

        connect_asset_cleanup(Story)
