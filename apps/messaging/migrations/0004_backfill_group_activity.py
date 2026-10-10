from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    """Groups created before group events had no last_message_at and were hidden from the chat list."""
    Conversation = apps.get_model("messaging", "Conversation")
    Conversation.objects.filter(is_group=True, last_message_at__isnull=True).update(last_message_at=F("created_at"))


class Migration(migrations.Migration):
    dependencies = [("messaging", "0003_groups_events_hidden")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
