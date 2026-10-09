"""Helpers for code that works on posts, reels and comments interchangeably."""


def kind_of(obj) -> str:
    return obj._meta.model_name  # "post" | "reel" | "comment"


def owner_of(obj):
    return getattr(obj, "author", None) or getattr(obj, "user", None)


def preview_of(obj, limit=80) -> str:
    text = getattr(obj, "caption", None) or getattr(obj, "content", None) or ""
    return text[:limit]
