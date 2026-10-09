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


def send_password_reset(email: str):
    user = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if user is None:
        return  # Do not reveal whether the address is registered.
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = settings.FLASHX["PASSWORD_RESET_URL"].format(uid=uid, token=token)
    try:
        send_mail(
            subject="Reset your FlashX password",
            message=(
                f"Hi {user.full_name or user.username},\n\n"
                f"Use this link to reset your FlashX password:\n{link}\n\n"
                "If you did not request this, you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception:
        logger.exception("Password reset email failed for user %s", user.pk)


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
