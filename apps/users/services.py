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


# Per-account sign-in protection: after this many wrong passwords within the
# window, the account refuses sign-in until the window passes. Counted in the
# shared cache, so it holds across server instances and source addresses.
LOGIN_MAX_FAILURES = 10
LOGIN_LOCK_SECONDS = 15 * 60


def _login_key(identifier: str) -> str:
    """One counter per account, whether it is named by username or email."""
    import hashlib

    identifier = (identifier or "").strip()
    pk = (
        User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
        .values_list("pk", flat=True)
        .first()
    )
    subject = f"user:{pk}" if pk else f"name:{identifier.lower()}"
    return "login-fail:" + hashlib.sha256(subject.encode()).hexdigest()


def login_locked(identifier: str) -> bool:
    from apps.core.cache import security_cache

    return int(security_cache.get(_login_key(identifier), 0)) >= LOGIN_MAX_FAILURES


def record_login_failure(identifier: str) -> None:
    from apps.core.cache import security_cache as cache

    key = _login_key(identifier)
    # add() starts the window; incr() keeps its original expiry.
    if not cache.add(key, 1, LOGIN_LOCK_SECONDS):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOGIN_LOCK_SECONDS)


def clear_login_failures(identifier: str) -> None:
    from apps.core.cache import security_cache

    security_cache.delete(_login_key(identifier))


RESET_CODE_MINUTES = 15
RESET_CODES_PER_HOUR = 3
RESET_CODE_MAX_ATTEMPTS = 5


def _hash_code(user_id, code: str) -> str:
    import hashlib
    import hmac

    return hmac.new(settings.SECRET_KEY.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def email_delivery_enabled() -> bool:
    """False when emails only go to the log (console/dummy backend): nobody receives them."""
    backend = settings.EMAIL_BACKEND.lower()
    return "console" not in backend and "dummy" not in backend


def send_password_reset(email: str):
    """
    Email a 6-digit code (valid 15 minutes) plus the classic reset link. Silent
    for unknown addresses so the endpoint never reveals who has an account.
    """
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from .models import PasswordResetCode

    if not email_delivery_enabled():
        logger.error(
            "Password reset requested but email delivery is off (EMAIL_BACKEND=%s). "
            "Set EMAIL_HOST, EMAIL_HOST_USER and EMAIL_HOST_PASSWORD.",
            settings.EMAIL_BACKEND,
        )
    user = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if user is None:
        return
    # A few codes an hour per account, so the endpoint can't be used to flood
    # someone's inbox. The response is the same either way.
    recent = PasswordResetCode.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(hours=1)).count()
    if recent >= RESET_CODES_PER_HOUR:
        logger.warning("Password reset codes limit reached for user %s", user.pk)
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


RESET_TOKEN_SALT = "flashx.password-reset"
RESET_TOKEN_SECONDS = 10 * 60


def _code_errors():
    from rest_framework.exceptions import ValidationError

    wrong = ValidationError({"code": "Wrong code. Check the email and try again."})
    expired = ValidationError({"code": "This code has expired. Request a new one."})
    return wrong, expired


def _check_reset_code(email: str, code: str):
    """The account and its live code record, or a ValidationError. Wrong tries count."""
    import hmac

    from django.utils import timezone

    from .models import PasswordResetCode

    wrong, expired = _code_errors()
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if user is None:
        # Same answer as a wrong code: never reveal whether the email has an account.
        raise wrong
    record = (
        PasswordResetCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if record is None or record.attempts >= RESET_CODE_MAX_ATTEMPTS:
        raise expired
    if not hmac.compare_digest(record.code_hash, _hash_code(user.pk, (code or "").strip())):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise expired if record.attempts >= RESET_CODE_MAX_ATTEMPTS else wrong
    return user, record


def verify_reset_code(email: str, code: str) -> str:
    """
    Step 2 of the code reset: check the emailed code. Returns a reset token,
    valid for 10 minutes and only for this code, that step 3 exchanges for
    the new password.
    """
    from django.core import signing

    user, record = _check_reset_code(email, code)
    return signing.dumps({"u": user.pk, "r": record.pk, "h": record.code_hash[:16]}, salt=RESET_TOKEN_SALT)


def _set_new_password(user, record, new_password: str):
    from django.contrib.auth import password_validation
    from django.utils import timezone

    password_validation.validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    blacklist_all_tokens(user)
    clear_login_failures(user.username)
    return user


def reset_password_with_token(reset_token: str, new_password: str):
    """Step 3: the token from verify_reset_code plus the new password. Single use."""
    from django.core import signing
    from django.utils import timezone
    from rest_framework.exceptions import ValidationError

    from .models import PasswordResetCode

    invalid = ValidationError({"reset_token": "This reset has expired. Start again and request a new code."})
    try:
        data = signing.loads(reset_token, salt=RESET_TOKEN_SALT, max_age=RESET_TOKEN_SECONDS)
    except signing.BadSignature:
        raise invalid from None
    record = (
        PasswordResetCode.objects.select_related("user")
        .filter(pk=data.get("r"), user_id=data.get("u"), used_at__isnull=True, expires_at__gt=timezone.now())
        .first()
    )
    if record is None or not record.user.is_active or record.code_hash[:16] != data.get("h"):
        raise invalid
    return _set_new_password(record.user, record, new_password)


def reset_password_with_code(email: str, code: str, new_password: str):
    """Code and new password in one request (kept for older app versions)."""
    user, record = _check_reset_code(email, code)
    return _set_new_password(user, record, new_password)


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


# ---------------------------------------------------------------------------
# Email confirmation for new accounts
# ---------------------------------------------------------------------------
EMAIL_CODE_MINUTES = 15
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODES_PER_HOUR = 5
EMAIL_CODE_RESEND_SECONDS = 45


def _email_code_hash(user_id, email: str, code: str) -> str:
    import hashlib
    import hmac

    message = f"verify:{user_id}:{email.lower()}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def send_email_code(user, *, enforce_cooldown=False) -> None:
    """Email a fresh 6-digit code to the account's address (earlier codes stop working)."""
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.exceptions import ServiceError

    from .models import EmailVerificationCode

    now = timezone.now()
    recent = EmailVerificationCode.objects.filter(user=user, created_at__gte=now - timedelta(hours=1))
    last = recent.order_by("-created_at").first()
    if enforce_cooldown and last and (now - last.created_at).total_seconds() < EMAIL_CODE_RESEND_SECONDS:
        wait = EMAIL_CODE_RESEND_SECONDS - int((now - last.created_at).total_seconds())
        raise ServiceError(
            f"Wait {wait} seconds before asking for another code.",
            code="throttled",
            status_code=429,
            details={"retry_after": wait},
        )
    if recent.count() >= EMAIL_CODES_PER_HOUR:
        raise ServiceError(
            "Too many codes requested. Try again in an hour.",
            code="throttled",
            status_code=429,
            details={"retry_after": 3600},
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    EmailVerificationCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    EmailVerificationCode.objects.create(
        user=user,
        email=user.email,
        code_hash=_email_code_hash(user.pk, user.email, code),
        expires_at=now + timedelta(minutes=EMAIL_CODE_MINUTES),
    )
    if not email_delivery_enabled():
        logger.error("Email code for user %s not sent: email delivery is off.", user.pk)
    name = user.full_name or user.username
    text = (
        f"Hi {name},\n\nYour FlashX confirmation code is: {code}\n\n"
        f"It expires in {EMAIL_CODE_MINUTES} minutes. If you did not create a FlashX account, ignore this email."
    )
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;background:#0A0D10;padding:32px;color:#FFFFFF">
      <div style="max-width:440px;margin:0 auto;background:#12161B;border-radius:16px;padding:28px">
        <div style="font-size:22px;font-weight:800;letter-spacing:-0.5px">
          Flash<span style="color:#C6F432">X</span>
        </div>
        <p style="color:#9AA3AD;margin:20px 0 8px">Hi {escape_html(name)}, confirm your email with this code</p>
        <div style="font-size:34px;font-weight:800;letter-spacing:8px;color:#C6F432">{code}</div>
        <p style="color:#9AA3AD;font-size:13px;margin-top:16px">It expires in {EMAIL_CODE_MINUTES} minutes.
        If you did not create a FlashX account, ignore this email.</p>
      </div>
    </div>"""
    try:
        send_mail(
            subject=f"{code} is your FlashX confirmation code",
            message=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html,
        )
    except Exception:
        logger.exception("Confirmation email failed for user %s", user.pk)


def escape_html(value: str) -> str:
    from django.utils.html import escape

    return escape(value)


def verify_email_code(user, code: str):
    """Confirm the account's email with the emailed code."""
    import hmac

    from django.utils import timezone
    from rest_framework.exceptions import ValidationError

    from .models import EmailVerificationCode

    if user.email_verified:
        return user
    wrong = ValidationError({"code": "Wrong code. Check the email and try again."})
    expired = ValidationError({"code": "This code has expired. Tap Resend code to get a new one."})
    record = (
        EmailVerificationCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if record is None or record.attempts >= EMAIL_CODE_MAX_ATTEMPTS or record.email.lower() != user.email.lower():
        raise expired
    if not hmac.compare_digest(record.code_hash, _email_code_hash(user.pk, user.email, (code or "").strip())):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise expired if record.attempts >= EMAIL_CODE_MAX_ATTEMPTS else wrong
    now = timezone.now()
    record.used_at = now
    record.save(update_fields=["used_at"])
    user.email_verified = True
    user.email_verified_at = now
    user.save(update_fields=["email_verified", "email_verified_at"])
    return user


def change_unconfirmed_email(user, email: str):
    """Fix a mistyped address before it is confirmed, and send a code there."""
    from rest_framework.exceptions import ValidationError

    if user.email_verified:
        raise ValidationError({"email": "Your email is already confirmed."})
    email = (email or "").strip().lower()
    if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise ValidationError({"email": "An account with this email already exists."})
    if email != user.email.lower():
        user.email = email
        user.save(update_fields=["email"])
    send_email_code(user, enforce_cooldown=False)
    return user


def purge_unconfirmed_accounts(older_than_days=7) -> int:
    """Delete self-registered accounts that never confirmed their email."""
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(days=older_than_days)
    stale = User.objects.filter(email_verified=False, date_joined__lt=cutoff, is_staff=False)
    count = stale.count()
    if count:
        stale.delete()
    return count
