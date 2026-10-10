from datetime import timedelta

from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.utils import timezone

from apps.follows.models import Follow
from apps.posts import selectors as post_selectors
from apps.posts.models import Hashtag, PostMedia, Visibility
from apps.reels import selectors as reel_selectors
from apps.users.selectors import base_users, with_follow_flags

MAX_Q = 100


def clean(q: str) -> str:
    return (q or "").strip()[:MAX_Q]


def search_users(viewer, q):
    q = q.lstrip("@")
    qs = (
        base_users(viewer)
        .filter(Q(username__icontains=q) | Q(full_name__icontains=q))
        .annotate(
            rank=Case(
                When(username__iexact=q, then=Value(0)),
                When(username__istartswith=q, then=Value(1)),
                When(full_name__istartswith=q, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
    )
    return with_follow_flags(qs, viewer).order_by("rank", "-is_verified", "-followers_count", "id")


def search_hashtags(q):
    q = q.lstrip("#").lower()
    return (
        Hashtag.objects.filter(name__icontains=q)
        .annotate(
            usage=F("posts_count") + F("reels_count"),
            rank=Case(
                When(name=q, then=Value(0)),
                When(name__startswith=q, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
        )
        .filter(usage__gt=0)
        .order_by("rank", "-usage", "name")
    )


def search_posts(viewer, q):
    tag = q.lstrip("#").lower()
    qs = post_selectors.visible_posts(viewer).filter(Q(caption__icontains=q) | Q(hashtags__name=tag)).distinct()
    return post_selectors.with_relations(qs, viewer).order_by("-created_at", "-id")


def search_reels(viewer, q):
    tag = q.lstrip("#").lower()
    qs = reel_selectors.visible_reels(viewer).filter(Q(caption__icontains=q) | Q(hashtags__name=tag)).distinct()
    return reel_selectors.with_relations(qs, viewer).order_by("-created_at", "-id")


def engagement_score():
    return F("likes_count") + F("comments_count") * 2 + F("shares_count") * 3 + F("saves_count") * 2


def explore_posts(viewer, tab="for_you", category=None):
    now = timezone.now()
    qs = post_selectors.visible_posts(viewer).filter(Exists(PostMedia.objects.filter(post=OuterRef("pk"))))
    following = Follow.objects.filter(follower=viewer).values("following_id")
    if tab == "following":
        qs = qs.filter(author_id__in=following)
        order = ("-created_at", "-id")
    elif tab == "trending":
        qs = qs.filter(visibility=Visibility.PUBLIC, created_at__gte=now - timedelta(days=3))
        order = ("-score", "-created_at", "-id")
    else:  # for_you: discovery outside your graph, recent and engaging
        qs = (
            qs.filter(visibility=Visibility.PUBLIC, created_at__gte=now - timedelta(days=30))
            .exclude(author_id__in=following)
            .exclude(author=viewer)
        )
        order = ("-score", "-created_at", "-id")
    if category:
        qs = qs.filter(category__slug=category)
    qs = qs.annotate(score=engagement_score())
    return post_selectors.with_relations(qs, viewer).order_by(*order)


def trending_hashtags(days=7, limit=15):
    since = timezone.now() - timedelta(days=days)
    post_uses = Count("posthashtag", filter=Q(posthashtag__created_at__gte=since), distinct=True)
    reel_uses = Count("reelhashtag", filter=Q(reelhashtag__created_at__gte=since), distinct=True)
    return (
        Hashtag.objects.annotate(recent=post_uses + reel_uses)
        .filter(recent__gt=0)
        .order_by("-recent", "-posts_count", "name")[:limit]
    )


def trending_reels(viewer, days=7, limit=10):
    since = timezone.now() - timedelta(days=days)
    qs = (
        reel_selectors.visible_reels(viewer)
        .filter(created_at__gte=since)
        .annotate(score=engagement_score() + F("views") / 10)
    )
    return reel_selectors.with_relations(qs, viewer).order_by("-score", "-created_at")[:limit]


def search_sounds(viewer, q):
    """Sounds already used on FlashX (catalogue songs and original sounds), most used first."""
    from apps.blocks.selectors import exclude_blocked
    from apps.music.models import Sound

    qs = Sound.objects.filter(is_active=True, source__in=[Sound.Source.JAMENDO, Sound.Source.FLASHX]).filter(
        Q(title__icontains=q) | Q(artist__icontains=q) | Q(owner__username__icontains=q)
    )
    qs = qs.filter(Q(owner__isnull=True) | Q(owner__is_active=True, owner__is_private=False))
    return exclude_blocked(qs, viewer, "owner_id").order_by("-uses_count", "-created_at")
