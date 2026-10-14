from django.db import migrations

# slug, name, coins, category, size
GIFTS = [
    ("sweets", "Sweets", 5, "gifts", "small"),
    ("cake", "Cake", 10, "gifts", "small"),
    ("rose", "Rose", 20, "gifts", "small"),
    ("heart", "Heart", 50, "gifts", "medium"),
    ("diamond", "Diamond", 100, "gifts", "medium"),
    ("rocket", "Rocket", 200, "gifts", "medium"),
    ("car", "Car", 500, "gifts", "large"),
    ("yacht", "Yacht", 1000, "gifts", "large"),
    ("dragon", "Dragon", 5000, "special", "epic"),
    ("castle", "Castle", 10000, "special", "epic"),
    ("galaxy", "Galaxy", 50000, "special", "epic"),
    ("crown", "Crown", 100000, "special", "epic"),
    ("confetti", "Confetti", 30, "events", "small"),
    ("trophy", "Trophy", 800, "events", "large"),
    ("fireworks", "Fireworks", 2500, "events", "large"),
]


def seed(apps, schema_editor):
    GiftItem = apps.get_model("wallet", "GiftItem")
    for i, (slug, name, coins, category, size) in enumerate(GIFTS):
        GiftItem.objects.update_or_create(
            slug=slug, defaults={"name": name, "coins": coins, "category": category, "size": size, "sort_order": i}
        )


class Migration(migrations.Migration):
    dependencies = [("wallet", "0001_wallet_and_gifts")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
