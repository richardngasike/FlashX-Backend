import re

from django.core.exceptions import ValidationError

USERNAME_RE = re.compile(r"^(?!.*\.\.)(?!\.)(?!.*\.$)[A-Za-z0-9._]{3,30}$")

RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "root",
        "support",
        "help",
        "flashx",
        "flash",
        "api",
        "settings",
        "explore",
        "reels",
        "stories",
        "messages",
        "notifications",
        "search",
        "login",
        "logout",
        "register",
        "signup",
        "me",
        "about",
        "privacy",
        "terms",
        "staff",
        "moderator",
        "system",
    }
)


def validate_username(value: str):
    if not USERNAME_RE.match(value or ""):
        raise ValidationError(
            "Usernames are 3-30 characters: letters, numbers, underscores and single periods "
            "(not at the start or end).",
            code="invalid_username",
        )
    if value.isdigit():
        raise ValidationError("Usernames cannot be only numbers.", code="invalid_username")
    if value.lower() in RESERVED_USERNAMES:
        raise ValidationError("This username is reserved.", code="reserved_username")
