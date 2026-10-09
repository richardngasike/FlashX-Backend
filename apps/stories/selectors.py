from django.db.models import Exists, OuterRef, Q

from apps.blocks.selectors import exclude_blocked
from apps.follows.models import Follow

from .models import Story, StoryReaction, StoryView


def active_stories(viewer):
    qs = exclude_blocked(Story.objects.active().select_related("author__profile_image"), viewer, "author_id")
    return qs.annotate(
        is_seen=Exists(StoryView.objects.filter(story=OuterRef("pk"), viewer=viewer)),
        my_reaction=StoryReaction.objects.filter(story=OuterRef("pk"), user=viewer).values("reaction")[:1],
    )


def tray(viewer):
    """Active stories grouped by author: you first, then unseen, then most recent."""
    following = Follow.objects.filter(follower=viewer).values("following_id")
    stories = list(active_stories(viewer).filter(Q(author_id__in=following) | Q(author=viewer)))
    groups = {}
    for s in sorted(stories, key=lambda s: (s.created_at, s.pk)):
        g = groups.setdefault(s.author_id, {"user": s.author, "stories": []})
        g["stories"].append(s)
    result = []
    for g in groups.values():
        g["has_unseen"] = any(not s.is_seen for s in g["stories"])
        g["latest_at"] = g["stories"][-1].created_at
        result.append(g)
    result.sort(key=lambda g: (g["user"].pk != viewer.pk, not g["has_unseen"], -g["latest_at"].timestamp()))
    return result
