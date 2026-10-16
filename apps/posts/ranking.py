"""
Home feed ranking ("For you").

Candidates are recent posts from people the viewer follows (and their own),
plus public posts from everyone else: the most engaging of the last two weeks
and the newest, so new creators get seen. Each candidate gets a score:

- engagement: likes, comments, shares and saves on a log scale;
- freshness: the score drops steadily with age;
- relationship: people you follow rank higher, your own post from the last
  few hours goes to the top;
- interest: authors and categories you have liked, saved or commented on;
- novelty: posts you already liked, or were shown at the top of your last
  refresh, step back so a refresh brings something new;
- variety: a small random factor seeded per refresh.

The ranked list is then spread out so one author doesn't fill the screen,
and cached per (viewer, seed) so paging through it stays stable. A new seed
(sent by the app on pull-to-refresh) gives a new mix.
"""

import hashlib
import math
import random
from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.db.models import F, Q
from django.utils import timezone

from apps.comments.models import Comment
from apps.follows.models import Follow
from apps.likes.models import Like
from apps.saves.models import SavedItem

from .models import Visibility

CACHE_SECONDS = 15 * 60
MAX_RANKED = 600
FOLLOWING_WINDOW = timedelta(days=30)
DISCOVERY_WINDOW = timedelta(days=14)
INTEREST_WINDOW = timedelta(days=60)

# Weights (tuned so a fresh post from someone you follow beats an old viral one).
W_ENGAGEMENT = 1.2
W_FOLLOWING = 3.0
W_OWN_RECENT = 6.0
W_AUTHOR_AFFINITY = 2.0
W_CATEGORY_AFFINITY = 1.2
W_NEW_POST = 0.8
P_ALREADY_LIKED = 4.0
P_SHOWN_LAST_TIME = 1.5
HOURS_PER_POINT = 18.0
JITTER = 1.8


def _key(viewer_id, seed) -> str:
    return "feed:" + hashlib.sha1(f"{viewer_id}|{seed}".encode(), usedforsecurity=False).hexdigest()


def _last_key(viewer_id) -> str:
    return f"feed:last:{viewer_id}"


def _affinities(viewer):
    """How much the viewer interacts with each author and category (0..1)."""
    since = timezone.now() - INTEREST_WINDOW
    authors = Counter()
    categories = Counter()
    likes = Like.objects.filter(user=viewer, post__isnull=False, created_at__gte=since)
    for author_id, category_id in likes.values_list("post__author_id", "post__category_id")[:500]:
        authors[author_id] += 1
        if category_id:
            categories[category_id] += 1
    for author_id, category_id in SavedItem.objects.filter(
        user=viewer, post__isnull=False, created_at__gte=since
    ).values_list("post__author_id", "post__category_id")[:300]:
        authors[author_id] += 2
        if category_id:
            categories[category_id] += 2
    for author_id, category_id in Comment.objects.filter(
        user=viewer, post__isnull=False, created_at__gte=since
    ).values_list("post__author_id", "post__category_id")[:300]:
        authors[author_id] += 2
        if category_id:
            categories[category_id] += 1
    authors.pop(viewer.pk, None)

    def normalise(c):
        top = max(c.values(), default=0)
        return {k: v / top for k, v in c.items()} if top else {}

    return normalise(authors), normalise(categories)


def _candidates(viewer, visible):
    now = timezone.now()
    following = set(Follow.objects.filter(follower=viewer).values_list("following_id", flat=True))
    fields = (
        "id",
        "author_id",
        "category_id",
        "created_at",
        "likes_count",
        "comments_count",
        "shares_count",
        "saves_count",
    )
    rows = {}
    near = visible.filter(Q(author_id__in=following) | Q(author=viewer), created_at__gte=now - FOLLOWING_WINDOW)
    for r in near.order_by("-created_at").values_list(*fields)[:400]:
        rows[r[0]] = r
    discovery = visible.filter(
        visibility=Visibility.PUBLIC, author__is_private=False, created_at__gte=now - DISCOVERY_WINDOW
    ).exclude(author=viewer)
    popular = discovery.annotate(
        _eng=F("likes_count") + 2 * F("comments_count") + 3 * F("shares_count") + 2 * F("saves_count")
    ).order_by("-_eng", "-created_at")
    for r in popular.values_list(*fields)[:300]:
        rows.setdefault(r[0], r)
    for r in discovery.order_by("-created_at").values_list(*fields)[:150]:
        rows.setdefault(r[0], r)
    if len(rows) < 40:
        # Quiet network: older posts keep the feed from running dry.
        older = visible.filter(Q(visibility=Visibility.PUBLIC, author__is_private=False) | Q(author_id__in=following))
        for r in older.order_by("-created_at").values_list(*fields)[:200]:
            rows.setdefault(r[0], r)
    return list(rows.values()), following


def _spread(ranked, gap=2):
    """Keep the same author out of the next [gap] positions where possible."""
    pending = list(ranked)
    out = []
    while pending:
        recent = {a for _, a in out[-gap:]}
        pick = next((i for i, (_, a) in enumerate(pending[:12]) if a not in recent), 0)
        out.append(pending.pop(pick))
    return [pid for pid, _ in out]


def rank(viewer, visible, seed):
    """Post ids for the viewer's For you feed, best first."""
    now = timezone.now()
    rows, following = _candidates(viewer, visible)
    if not rows:
        return []
    author_aff, category_aff = _affinities(viewer)
    ids = [r[0] for r in rows]
    liked = set(Like.objects.filter(user=viewer, post_id__in=ids).values_list("post_id", flat=True))
    shown_before = set(cache.get(_last_key(viewer.pk)) or [])
    rng_salt = hashlib.sha1(str(seed).encode(), usedforsecurity=False).hexdigest()

    scored = []
    for pid, author_id, category_id, created_at, likes, comments, shares, saves in rows:
        age_h = max(0.0, (now - created_at).total_seconds() / 3600)
        engagement = math.log1p(likes + 2 * comments + 3 * shares + 2 * saves)
        score = W_ENGAGEMENT * engagement - age_h / HOURS_PER_POINT
        if author_id == viewer.pk:
            score += W_OWN_RECENT if age_h < 3 else 0
        elif author_id in following:
            score += W_FOLLOWING
        score += W_AUTHOR_AFFINITY * author_aff.get(author_id, 0)
        if category_id:
            score += W_CATEGORY_AFFINITY * category_aff.get(category_id, 0)
        if age_h < 2 and engagement < 1.5:
            score += W_NEW_POST
        if pid in liked:
            score -= P_ALREADY_LIKED
        if pid in shown_before:
            score -= P_SHOWN_LAST_TIME
        score += random.Random(f"{rng_salt}:{pid}").random() * JITTER
        scored.append((score, pid, author_id))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return _spread([(pid, author) for _, pid, author in scored[:MAX_RANKED]])


def ranked_ids(viewer, visible, seed=None):
    """Cached ranking for (viewer, seed). A missing seed changes hourly."""
    if not seed:
        seed = f"{timezone.now():%Y%m%d%H}"
    seed = str(seed)[:64]
    key = _key(viewer.pk, seed)
    ids = cache.get(key)
    if ids is None:
        ids = rank(viewer, visible, seed)
        cache.set(key, ids, CACHE_SECONDS)
        # Remember the top of this refresh so the next one leads with something else.
        cache.set(_last_key(viewer.pk), ids[:20], 24 * 3600)
    return ids


def page_posts(ids, base_qs):
    """Posts for a page of ids, in that order (anything no longer visible is dropped)."""
    by_id = {p.pk: p for p in base_qs.filter(pk__in=ids)}
    return [by_id[i] for i in ids if i in by_id]
