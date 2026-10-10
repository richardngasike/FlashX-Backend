import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db.models import Q
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)
User = get_user_model()


def issue_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def authenticate_identifier(identifier: str, password: str):
    """Username or email login. Runs the hasher even on miss to blunt timing attacks."""
    identifier = (identifier or "").strip()
    user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
    if user is None:
        User().set_password(password)
        return None
    if not user.check_password(password) or not user.is_active:
        return None
    return user


RESET_CODE_MINUTES = 15
RESET_CODE_MAX_ATTEMPTS = 5


def _hash_code(user_id, code: str) -> str:
    import hashlib
    import hmac

    return hmac.new(settings.SECRET_KEY.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def send_password_reset(email: str):
    """
    Email a 6-digit code (valid 15 minutes) plus the classic reset link. Silent
    for unknown addresses so the endpoint never reveals who has an account.
    """
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from .models import PasswordResetCode

    user = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if user is None:
        return
    code = f"{secrets.randbelow(1_000_000):06d}"
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    PasswordResetCode.objects.create(
        user=user,
        code_hash=_hash_code(user.pk, code),
        expires_at=timezone.now() + timedelta(minutes=RESET_CODE_MINUTES),
    )
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = settings.FLASHX["PASSWORD_RESET_URL"].format(uid=uid, token=token)
    name = user.full_name or user.username
    text = (
        f"Hi {name},\n\n"
        f"Your FlashX password reset code is: {code}\n\n"
        f"Enter it in the app within {RESET_CODE_MINUTES} minutes. You can also use this link:\n{link}\n\n"
        "If you did not ask to reset your password, ignore this email. Your password stays the same."
    )
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;background:#0A0D10;padding:32px;color:#FFFFFF">
      <div style="max-width:440px;margin:0 auto;background:#12161B;border-radius:16px;padding:28px">
        <div style="font-size:22px;font-weight:800;letter-spacing:-0.5px">
          Flash<span style="color:#C6F432">X</span>
        </div>
        <p style="color:#9AA3AD;margin:20px 0 8px">Hi {name}, your password reset code is</p>
        <div style="font-size:34px;font-weight:800;letter-spacing:8px;color:#C6F432">{code}</div>
        <p style="color:#9AA3AD;font-size:13px;margin-top:16px">It expires in {RESET_CODE_MINUTES} minutes.
        If you did not ask to reset your password, ignore this email.</p>
      </div>
    </div>"""
    try:
        send_mail(
            subject=f"{code} is your FlashX reset code",
            message=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html,
        )
    except Exception:
        logger.exception("Password reset email failed for user %s", user.pk)


def reset_password_with_code(email: str, code: str, new_password: str):
    import hmac

    from django.contrib.auth import password_validation
    from django.utils import timezone
    from rest_framework.exceptions import ValidationError

    from .models import PasswordResetCode

    invalid = ValidationError({"code": "That code is wrong or has expired. Request a new one."})
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if user is None:
        raise invalid
    record = (
        PasswordResetCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if record is None or record.attempts >= RESET_CODE_MAX_ATTEMPTS:
        raise invalid
    if not hmac.compare_digest(record.code_hash, _hash_code(user.pk, (code or "").strip())):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise invalid
    password_validation.validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    blacklist_all_tokens(user)
    return user


def reset_password(uid: str, token: str, new_password: str):
    from django.contrib.auth import password_validation
    from rest_framework.exceptions import ValidationError

    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)), is_active=True)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        raise ValidationError({"token": "This reset link is invalid or has expired."}) from None
    if not default_token_generator.check_token(user, token):
        raise ValidationError({"token": "This reset link is invalid or has expired."})
    password_validation.validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    blacklist_all_tokens(user)
    return user


def blacklist_all_tokens(user):
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    for token in OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False):
        BlacklistedToken.objects.get_or_create(token=token)
