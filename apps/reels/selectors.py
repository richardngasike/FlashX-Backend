from django.db.models import Exists, OuterRef, Value

from apps.blocks.selectors import exclude_blocked
from apps.follows.models import Follow
from apps.likes.models import Like
from apps.saves.models import SavedItem

from .models import Reel


def visible_reels(viewer=None):
    qs = Reel.objects.filter(is_hidden=False, author__is_active=True)
    return exclude_blocked(qs, viewer, "author_id")


def with_relations(qs, viewer):
    qs = qs.select_related("author__profile_image").prefetch_related("hashtags")
    if not viewer or not viewer.is_authenticated:
        return qs.annotate(is_liked=Value(False), is_saved=Value(False), author_is_following=Value(False))
    return qs.annotate(
        is_liked=Exists(Like.objects.filter(user=viewer, reel=OuterRef("pk"))),
        is_saved=Exists(SavedItem.objects.filter(user=viewer, reel=OuterRef("pk"))),
        author_is_following=Exists(Follow.objects.filter(follower=viewer, following=OuterRef("author_id"))),
    )
