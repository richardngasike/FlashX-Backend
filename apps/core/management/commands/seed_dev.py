"""
Development seed data.

    python manage.py seed_dev                 # users, follows, text posts, comments, categories
    python manage.py seed_dev --with-media    # + image posts, stories and reels from your Cloudinary
                                              #   account's built-in "samples/" assets
    python manage.py seed_dev --reset         # remove previous seed users first

Seed users all have emails at @seed.flashx.local and share --password.
Seed media rows are marked unmanaged so FlashX never deletes the shared samples.
"""

import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.comments.services import create_comment
from apps.follows.services import follow
from apps.likes.services import like
from apps.media import cloudinary_service as cld
from apps.media.models import MediaAsset, MediaPurpose
from apps.posts.models import Category, Post
from apps.posts.services import create_post
from apps.reels.services import create_reel
from apps.stories.models import Story

User = get_user_model()
SEED_DOMAIN = "seed.flashx.local"

USERS = [
    ("richyict", "Richard Ngasike", "Mobile App Developer | Ethical Hacker", "https://richardngasike.co.ke", True),
    ("faith_wanjiku", "Faith Wanjiku", "Coffee, code and Nairobi sunsets.", "", False),
    ("kelvin.m", "Kelvin M.", "Product designer. Shapes and systems.", "", False),
    ("njeri_k", "Njeri Kamau", "Food stories from Nairobi kitchens.", "", False),
    ("brian254", "Brian Otieno", "Football on weekends, Flutter on weekdays.", "", False),
    ("lilian.a", "Lilian Achieng", "Lifestyle and slow mornings.", "", False),
    ("samburu_traveller", "Lemayian Lekupe", "Northern Kenya through my lens.", "", True),
    ("nairobi_vibes", "Nairobi Vibes", "The city, every day.", "", True),
    ("skater_ke", "Skater KE", "Small steps. Big dreams.", "", False),
    ("willy_o", "Willy Odhiambo", "Music producer. Beats over everything.", "", False),
    ("amina.codes", "Amina Hassan", "Backend engineer. Django and data.", "", False),
    ("tech_and_life", "Tech & Life", "Tech news and life notes.", "", False),
]

CATEGORIES = [
    ("Travel", "travel"),
    ("Tech", "tech"),
    ("Music", "music"),
    ("Lifestyle", "lifestyle"),
    ("Food", "food"),
    ("Sports", "sports"),
]

TEXT_POSTS = [
    (
        "samburu_traveller",
        "The beauty of home. Samburu never disappoints. #samburu #kenya #travel",
        "Samburu, Kenya",
        "travel",
    ),
    ("nairobi_vibes", "Matatu art is the best gallery in the city. #nairobi #art", "Nairobi, Kenya", "lifestyle"),
    (
        "brian254",
        "Shipped my first Flutter release today. Riverpod or Provider? Fight me. #flutter #mobiledev",
        "",
        "tech",
    ),
    ("njeri_k", "Sunday pilau done right: toast the spices first. #food #nairobi", "Nairobi, Kenya", "food"),
    ("amina.codes", "Index your foreign keys. Your future self will thank you. #django #postgres", "", "tech"),
    ("willy_o", "New beat drops Friday. Afro-house with a little benga in the bassline. #music", "", "music"),
    (
        "faith_wanjiku",
        "Morning run around Karura. Ten kilometres, zero regrets. #fitness #karura",
        "Karura Forest",
        "sports",
    ),
    ("kelvin.m", "Solid surfaces, one accent colour, no gradients. Good UI is restraint. #design", "", "tech"),
    ("lilian.a", "Slow mornings are a productivity strategy. #lifestyle", "", "lifestyle"),
    (
        "richyict",
        "Building FlashX: Django + Flutter + Cloudinary. Create. Share. Connect. #flashx #buildinpublic",
        "Nairobi, Kenya",
        "tech",
    ),
    ("skater_ke", "Small steps. Big dreams. #skate #nairobi", "", "sports"),
    ("tech_and_life", "Hot take: the best feature is the one you deleted. #tech", "", "tech"),
]

COMMENTS = [
    "This is beautiful!",
    "Love this.",
    "Facts.",
    "Where is this exactly?",
    "Need the recipe.",
    "Big moves!",
    "Saving this for later.",
    "So clean.",
    "Proud of you!",
    "Can't wait.",
]


class Command(BaseCommand):
    help = "Create realistic development data for FlashX."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="FlashX-dev-2026!")
        parser.add_argument("--with-media", action="store_true", help="Use your Cloudinary account's samples/.")
        parser.add_argument("--reset", action="store_true", help="Delete existing seed users first.")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG=False.")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("Refusing to seed with DEBUG=False. Pass --force if you really mean it.")
        random.seed(42)
        if opts["reset"]:
            n, _ = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}").delete()
            self.stdout.write(f"Removed previous seed data ({n} rows).")

        with transaction.atomic():
            categories = self._categories()
            users = self._users(opts["password"])
            self._follows(users)
            posts = self._text_posts(users, categories)
            self._engagement(users, posts)

        media_summary = "skipped (run with --with-media)"
        if opts["with_media"]:
            media_summary = self._media(users, categories)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(users)} users, {len(posts)} text posts. Media: {media_summary}.\n"
                f"Log in as any seed user, e.g. richyict / {opts['password']}"
            )
        )

    def _categories(self):
        out = {}
        for i, (name, slug) in enumerate(CATEGORIES):
            out[slug], _ = Category.objects.update_or_create(
                slug=slug, defaults={"name": name, "icon": slug, "order": i, "is_active": True}
            )
        return out

    def _users(self, password):
        users = {}
        for username, full_name, bio, website, verified in USERS:
            user = User.objects.filter(username__iexact=username).first()
            if user is None:
                user = User.objects.create_user(
                    username=username,
                    email=f"{username.replace('.', '_')}@{SEED_DOMAIN}",
                    full_name=full_name,
                    password=password,
                )
            user.bio, user.website, user.is_verified = bio, website, verified
            user.last_seen_at = timezone.now() - timedelta(minutes=random.randint(0, 600))
            user.save()
            users[username] = user
        return users

    def _follows(self, users):
        people = list(users.values())
        for u in people:
            for other in random.sample(people, k=min(len(people), 7)):
                if other.pk != u.pk:
                    follow(u, other)

    def _text_posts(self, users, categories):
        posts = []
        for username, caption, location, cat in TEXT_POSTS:
            author = users[username]
            existing = Post.objects.filter(author=author, caption=caption).first()
            posts.append(existing or create_post(author, caption=caption, location=location, category=categories[cat]))
        return posts

    def _engagement(self, users, posts):
        people = list(users.values())
        for post in posts:
            for u in random.sample(people, k=random.randint(2, 8)):
                like(u, post)
            for u in random.sample(people, k=random.randint(0, 3)):
                if post.comments_enabled and u.pk != post.author_id:
                    create_comment(u, post, random.choice(COMMENTS))

    def _media(self, users, categories):
        if not cld.is_configured():
            return "skipped (Cloudinary is not configured)"
        try:
            images = cld.list_resources("samples/", "image", 60)
            videos = cld.list_resources("samples/", "video", 20)
        except Exception as exc:  # noqa: BLE001 - report and continue
            return f"skipped (could not list Cloudinary samples: {exc})"
        if not images and not videos:
            return "skipped (no assets under samples/ in your Cloudinary account)"

        people = list(users.values())
        made = {"posts": 0, "stories": 0, "reels": 0}

        def asset(owner, meta, purpose):
            obj, _ = MediaAsset.objects.get_or_create(
                public_id=meta["public_id"],
                defaults=dict(
                    owner=owner,
                    purpose=purpose,
                    resource_type=meta["resource_type"],
                    version=meta["version"],
                    format=meta["format"],
                    secure_url=meta["secure_url"],
                    bytes=meta["bytes"],
                    width=meta["width"],
                    height=meta["height"],
                    duration=meta["duration"],
                    is_managed=False,
                ),
            )
            return obj

        cat_slugs = list(categories)
        with transaction.atomic():
            for i, meta in enumerate(images):
                owner = people[i % len(people)]
                a = asset(owner, meta, MediaPurpose.POST)
                if a.is_attached:
                    continue
                if i % 4 == 3:
                    a.purpose = MediaPurpose.STORY
                    a.save(update_fields=["purpose"])
                    Story.objects.create(
                        author=owner,
                        asset=a,
                        cloudinary_url=a.secure_url,
                        cloudinary_public_id=a.public_id,
                        media_type="image",
                        width=a.width,
                        height=a.height,
                    )
                    a.is_attached = True
                    a.save(update_fields=["is_attached"])
                    made["stories"] += 1
                else:
                    create_post(
                        owner,
                        caption=f"Moments worth sharing #{cat_slugs[i % len(cat_slugs)]}",
                        category=categories[cat_slugs[i % len(cat_slugs)]],
                        media_ids=[a.pk],
                    )
                    made["posts"] += 1
            for i, meta in enumerate(videos):
                owner = people[(i + 3) % len(people)]
                a = asset(owner, meta, MediaPurpose.REEL)
                if a.is_attached:
                    continue
                create_reel(owner, media_id=a.pk, caption="Small steps. Big dreams. #reels #flashx")
                made["reels"] += 1
        return ", ".join(f"{v} {k}" for k, v in made.items())
