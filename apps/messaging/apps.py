from django.apps import AppConfig


class MessagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.messaging"
    label = "messaging"
    verbose_name = "Messages"

    def ready(self):
        from apps.media.signals import connect_asset_cleanup

        from .models import Message

        connect_asset_cleanup(Message)
