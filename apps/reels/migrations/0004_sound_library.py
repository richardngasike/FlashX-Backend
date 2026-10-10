import django.db.models.deletion
from django.db import migrations, models


def copy_legacy(apps, schema_editor):
    """Sounds picked with the previous app version were stored inline; give each its own Sound row."""
    Model = apps.get_model("reels", "reel")
    Sound = apps.get_model("music", "Sound")
    for obj in Model.objects.exclude(legacy_sound__isnull=True).iterator():
        data = obj.legacy_sound or {}
        raw_id = str(data.get("id") or "")
        source, _, external = raw_id.partition(":")
        if source not in ("deezer", "itunes", "jamendo") or not external or not data.get("preview_url"):
            continue
        sound, _ = Sound.objects.get_or_create(
            source=source,
            external_id=external,
            defaults={
                "title": (data.get("title") or "")[:200] or "Sound",
                "artist": (data.get("artist") or "")[:200],
                "cover_url": (data.get("cover") or "")[:500],
                "audio_url": data["preview_url"][:500],
                # Store previews are not licensed for reuse: kept on the old post, not offered again.
                "is_active": source == "jamendo",
            },
        )
        obj.sound_id = sound.pk
        obj.save(update_fields=["sound"])
        Sound.objects.filter(pk=sound.pk).update(uses_count=models.F("uses_count") + 1)


class Migration(migrations.Migration):
    dependencies = [
        ("reels", "0003_reel_sound"),
        ("music", "0001_sounds"),
    ]

    operations = [
        migrations.RenameField(model_name="reel", old_name="sound", new_name="legacy_sound"),
        migrations.AddField(
            model_name="reel",
            name="sound",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reels",
                to="music.sound",
            ),
        ),
        migrations.AddField(model_name="reel", name="sound_start", field=models.FloatField(default=0)),
        migrations.AddField(model_name="reel", name="sound_volume", field=models.FloatField(default=1)),
        migrations.AddField(model_name="reel", name="original_volume", field=models.FloatField(default=1)),
        migrations.AddField(
            model_name="reel",
            name="allow_sound_reuse",
            field=models.BooleanField(default=True, help_text="Others may use this video's original sound."),
        ),
        migrations.RunPython(copy_legacy, migrations.RunPython.noop),
        migrations.RemoveField(model_name="reel", name="legacy_sound"),
    ]
