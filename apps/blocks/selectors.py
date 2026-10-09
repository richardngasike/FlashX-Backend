"""Read helpers that keep blocked users out of each other's way."""

from django.db.models import Q

from .models import Block


def _authenticated(user):
    return user is not None and getattr(user, "is_authenticated", False)


def blocked_by_me(user):
    """Subquery of ids ``user`` has blocked."""
    return Block.objects.filter(blocker=user).values("blocked_id")


def blocking_me(user):
    """Subquery of ids that have blocked ``user``."""
    return Block.objects.filter(blocked=user).values("blocker_id")


def exclude_blocked(qs, viewer, field="pk"):
    """Drop rows whose ``field`` points at a user blocked by, or blocking, ``viewer``."""
    if not _authenticated(viewer):
        return qs
    return qs.exclude(**{f"{field}__in": blocked_by_me(viewer)}).exclude(**{f"{field}__in": blocking_me(viewer)})


def hidden_user_ids(user) -> set:
    """Ids hidden from ``user`` in either direction (for in-memory checks)."""
    if not _authenticated(user):
        return set()
    ids = set()
    for blocker_id, blocked_id in Block.objects.filter(Q(blocker=user) | Q(blocked=user)).values_list(
        "blocker_id", "blocked_id"
    ):
        ids.add(blocked_id if blocker_id == user.pk else blocker_id)
    return ids


def is_blocked_between(a, b) -> bool:
    if a is None or b is None or a.pk == b.pk:
        return False
    return Block.objects.filter(Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a)).exists()


def has_blocked(blocker, blocked) -> bool:
    return Block.objects.filter(blocker=blocker, blocked=blocked).exists()
