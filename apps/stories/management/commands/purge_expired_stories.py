from django.core.management.base import BaseCommand

from apps.stories.services import purge_expired


class Command(BaseCommand):
    help = "Delete stories older than their expiry (and their Cloudinary files). Schedule every 15 minutes."

    def handle(self, *args, **options):
        count = purge_expired()
        self.stdout.write(self.style.SUCCESS(f"Purged {count} expired stories."))
