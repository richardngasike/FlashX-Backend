from django.db.models import Exists, OuterRef, Value

from apps.blocks.selectors import exclude_blocked
from apps.likes.models import Like

from .models import Comment


def visible_comments(viewer):
    qs = Comment.objects.filter(is_hidden=False, user__is_active=True).select_related(
        "user__profile_image", "post", "reel"
    )
    qs = exclude_blocked(qs, viewer, "user_id")
    if viewer and viewer.is_authenticated:
        return qs.annotate(is_liked=Exists(Like.objects.filter(user=viewer, comment=OuterRef("pk"))))
    return qs.annotate(is_liked=Value(False))
