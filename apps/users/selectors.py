from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, Value

from apps.follows.models import Follow

User = get_user_model()


def base_users():
    return User.objects.filter(is_active=True).select_related("profile_image", "cover_image")


def with_follow_flags(qs, viewer):
    if not viewer or not viewer.is_authenticated:
        return qs.annotate(is_following=Value(False), follows_you=Value(False))
    return qs.annotate(
        is_following=Exists(Follow.objects.filter(follower=viewer, following=OuterRef("pk"))),
        follows_you=Exists(Follow.objects.filter(follower=OuterRef("pk"), following=viewer)),
    )


def suggested_users(viewer, limit=20):
    """Ranked by mutual connections (people your follows follow), then popularity."""
    my_following = Follow.objects.filter(follower=viewer).values("following_id")
    qs = (
        base_users()
        .exclude(pk=viewer.pk)
        .exclude(pk__in=my_following)
        .annotate(mutuals=Count("follower_set", filter=Q(follower_set__follower_id__in=my_following), distinct=True))
        .order_by("-mutuals", "-is_verified", "-followers_count", "-created_at")
    )
    return with_follow_flags(qs, viewer)[:limit]
