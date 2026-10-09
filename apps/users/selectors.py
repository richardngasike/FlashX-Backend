from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, Value

from apps.blocks.selectors import exclude_blocked
from apps.follows.models import Follow

User = get_user_model()


def base_users(viewer=None):
    """Active users; with ``viewer``, also hides anyone blocked by or blocking them."""
    qs = User.objects.filter(is_active=True).select_related("profile_image", "cover_image")
    return exclude_blocked(qs, viewer)


def with_follow_flags(qs, viewer):
    if not viewer or not viewer.is_authenticated:
        return qs.annotate(is_following=Value(False), follows_you=Value(False))
    return qs.annotate(
        is_following=Exists(Follow.objects.filter(follower=viewer, following=OuterRef("pk"))),
        follows_you=Exists(Follow.objects.filter(follower=OuterRef("pk"), following=viewer)),
    )


def suggested_users_queryset(viewer):
    """
    People you may know: accounts you don't follow yet, ranked by people who
    follow you, then mutual connections (people your follows follow), then
    verification and popularity. Blocked accounts never appear.
    """
    my_following = Follow.objects.filter(follower=viewer).values("following_id")
    qs = (
        base_users(viewer)
        .exclude(pk=viewer.pk)
        .exclude(pk__in=my_following)
        .annotate(mutuals=Count("follower_set", filter=Q(follower_set__follower_id__in=my_following), distinct=True))
    )
    return with_follow_flags(qs, viewer).order_by(
        "-follows_you", "-mutuals", "-is_verified", "-followers_count", "-created_at", "-id"
    )


def suggested_users(viewer, limit=20):
    return suggested_users_queryset(viewer)[:limit]


def suggestion_context(viewer, users) -> dict:
    """
    Per suggested user: how many people you follow also follow them, up to two
    of their names, and a short reason line for the card.
    """
    users = list(users)
    ids = [u.pk for u in users]
    if not ids:
        return {}
    my_following = Follow.objects.filter(follower=viewer).values("following_id")
    names = {}
    rows = (
        Follow.objects.filter(following_id__in=ids, follower_id__in=my_following, follower__is_active=True)
        .order_by("-follower__followers_count", "follower_id")
        .values_list("following_id", "follower__username", "follower__full_name")
    )
    for target_id, username, full_name in rows:
        bucket = names.setdefault(target_id, [])
        if len(bucket) < 2:
            bucket.append((full_name or "").split(" ")[0] or username)

    context = {}
    for user in users:
        count = getattr(user, "mutuals", None)
        if count is None:
            count = Follow.objects.filter(following=user, follower_id__in=my_following).count()
        preview = names.get(user.pk, [])
        if getattr(user, "follows_you", False):
            reason = "Follows you"
        elif count and preview:
            extra = count - len(preview)
            reason = (
                "Followed by " + " and ".join(preview) if extra <= 0 else f"Followed by {preview[0]} + {count - 1} more"
            )
        elif user.is_verified:
            reason = "Verified on FlashX"
        elif user.followers_count >= 10:
            reason = "Popular on FlashX"
        else:
            reason = "New to FlashX"
        context[user.pk] = {"mutual_count": count, "mutual_preview": preview, "reason": reason}
    return context
