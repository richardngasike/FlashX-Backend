"""Kenyan mobile numbers, stored in the international form M-PESA expects."""

import re

from django.core.exceptions import ValidationError

# 07XXXXXXXX / 01XXXXXXXX, with or without +254 / 254, spaces or dashes.
_KE_MOBILE = re.compile(r"^(?:\+?254|0)?([17]\d{8})$")


def normalize_ke_phone(raw: str) -> str:
    """Return 2547XXXXXXXX or 2541XXXXXXXX, or raise ValidationError."""
    digits = re.sub(r"[\s\-().]", "", str(raw or ""))
    match = _KE_MOBILE.match(digits)
    if not match:
        raise ValidationError("Enter a valid Safaricom or other Kenyan mobile number, e.g. 0712 345 678.")
    return "254" + match.group(1)


def display_phone(stored: str) -> str:
    """2547XXXXXXXX -> 07XX XXX XXX for people to read."""
    if not stored or len(stored) != 12:
        return stored or ""
    local = "0" + stored[3:]
    return f"{local[:4]} {local[4:7]} {local[7:]}"
