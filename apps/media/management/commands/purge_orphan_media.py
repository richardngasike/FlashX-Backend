from django.core.management.base import BaseCommand

from apps.media.services import purge_orphans


class Command(BaseCommand):
    help = (
        "Delete uploads that were never attached to content (and their Cloudinary files). "
        "On Vercel this runs from /api/cron/maintenance/."
    )

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        count = purge_orphans(older_than_hours=opts["older_than_hours"], dry_run=opts["dry_run"])
        if opts["dry_run"]:
            self.stdout.write(f"{count} orphaned uploads would be deleted.")
            return
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} orphaned uploads."))
