from django.db.models import Exists, OuterRef, Prefetch, Q, Value

from apps.follows.models import Follow
from apps.likes.models import Like
from apps.saves.models import SavedItem
from apps.users.selectors import base_users

from .models import Post, PostMedia, Visibility


def visible_posts(viewer):
    qs = Post.objects.filter(is_hidden=False, author__is_active=True)
    if not viewer or not viewer.is_authenticated:
        return qs.filter(visibility=Visibility.PUBLIC)
    following = Follow.objects.filter(follower=viewer).values("following_id")
    return qs.filter(
        Q(visibility=Visibility.PUBLIC) | Q(author=viewer) | Q(visibility=Visibility.FOLLOWERS, author_id__in=following)
    )


def with_relations(qs, viewer):
    qs = qs.select_related("author__profile_image", "category").prefetch_related(
        Prefetch("media", queryset=PostMedia.objects.order_by("order")),
        Prefetch("tagged_users", queryset=base_users()),
        "hashtags",
    )
    if not viewer or not viewer.is_authenticated:
        return qs.annotate(is_liked=Value(False), is_saved=Value(False), author_is_following=Value(False))
    return qs.annotate(
        is_liked=Exists(Like.objects.filter(user=viewer, post=OuterRef("pk"))),
        is_saved=Exists(SavedItem.objects.filter(user=viewer, post=OuterRef("pk"))),
        author_is_following=Exists(Follow.objects.filter(follower=viewer, following=OuterRef("author_id"))),
    )


def home_feed(viewer):
    """Posts from people the viewer follows, plus the viewer's own."""
    following = Follow.objects.filter(follower=viewer).values("following_id")
    qs = visible_posts(viewer).filter(Q(author_id__in=following) | Q(author=viewer))
    return with_relations(qs, viewer)
