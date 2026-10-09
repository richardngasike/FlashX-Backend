"""Trigram GIN indexes so icontains search stays fast as tables grow."""
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

INDEXES = [
    ("users_user", "username", "users_username_trgm"),
    ("users_user", "full_name", "users_full_name_trgm"),
    ("posts_post", "caption", "posts_caption_trgm"),
    ("posts_hashtag", "name", "posts_hashtag_name_trgm"),
    ("reels_reel", "caption", "reels_caption_trgm"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0002_initial"),
        ("users", "0001_initial"),
        ("posts", "0001_initial"),
        ("reels", "0001_initial"),
    ]

    operations = [TrigramExtension()] + [
        migrations.RunSQL(
            sql=f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin (UPPER({column}) gin_trgm_ops);",
            reverse_sql=f"DROP INDEX IF EXISTS {name};",
        )
        for table, column, name in INDEXES
    ]
