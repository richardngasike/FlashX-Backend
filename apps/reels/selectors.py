import hashlib

from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.expressions import RawSQL
from django.utils import timezone

from apps.blocks.selectors import exclude_blocked
from apps.follows.models import Follow
from apps.likes.models import Like
from apps.saves.models import SavedItem

from .models import Reel


def visible_reels(viewer=None):
    qs = Reel.objects.filter(is_hidden=False, author__is_active=True)
    if not viewer or not viewer.is_authenticated:
        return qs.filter(author__is_private=False)
    following = Follow.objects.filter(follower=viewer).values("following_id")
    qs = qs.filter(Q(author__is_private=False) | Q(author=viewer) | Q(author_id__in=following))
    return exclude_blocked(qs, viewer, "author_id")


def with_relations(qs, viewer):
    qs = qs.select_related("author__profile_image", "sound").prefetch_related("hashtags")
    if not viewer or not viewer.is_authenticated:
        return qs.annotate(is_liked=Value(False), is_saved=Value(False), author_is_following=Value(False))
    return qs.annotate(
        is_liked=Exists(Like.objects.filter(user=viewer, reel=OuterRef("pk"))),
        is_saved=Exists(SavedItem.objects.filter(user=viewer, reel=OuterRef("pk"))),
        author_is_following=Exists(Follow.objects.filter(follower=viewer, following=OuterRef("author_id"))),
    )


def for_you(viewer, seed=None):
    """
    Ranked For You feed: reels the viewer hasn't watched come first, then a
    hot score (engagement, decayed by age) plus a per-session shuffle. A new
    ``seed`` (sent by the app on every pull-to-refresh) gives a fresh mix;
    the same seed keeps pagination stable.
    """
    from .models import ReelView

    if not seed:
        seed = f"{getattr(viewer, 'pk', 0)}-{timezone.now():%Y%m%d%H}"
    salt = hashlib.sha1(str(seed).encode()).hexdigest()[:12]
    qs = visible_reels(viewer)
    seen = Exists(ReelView.objects.filter(reel=OuterRef("pk"), user=viewer))
    own = RawSQL("reels_reel.author_id = %s", (viewer.pk,))
    hot = RawSQL(
        """
        ln(1 + reels_reel.likes_count * 2 + reels_reel.comments_count * 3
             + reels_reel.shares_count * 4 + reels_reel.saves_count * 3 + reels_reel.views / 10.0)
        - extract(epoch from (now() - reels_reel.created_at)) / 172800.0
        + 2.5 * (('x' || substr(md5(reels_reel.id::text || %s), 1, 7))::bit(28)::int / 268435456.0)
        """,
        (salt,),
    )
    return qs.annotate(is_seen=seen, is_own=own, hot=hot).order_by("is_seen", "is_own", "-hot", "-id")
