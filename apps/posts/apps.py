from django.apps import AppConfig


class PostsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.posts"
    label = "posts"

    def ready(self):
        from apps.media.signals import connect_asset_cleanup

        from .models import PostMedia

        connect_asset_cleanup(PostMedia)
