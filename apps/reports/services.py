from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.exceptions import ServiceError

from .models import Report

User = get_user_model()


def _target_model(target_type):
    from apps.comments.models import Comment
    from apps.posts.models import Post
    from apps.reels.models import Reel

    return {"post": Post, "reel": Reel, "comment": Comment, "user": User}[target_type]


def _target_owner_id(target_type, target):
    if target_type == "user":
        return target.pk
    return getattr(target, "author_id", None) or getattr(target, "user_id", None)


def file_report(reporter, *, target_type, target_id, reason, details=""):
    model = _target_model(target_type)
    target = model.objects.filter(pk=target_id).first()
    if target is None or (target_type == "user" and not target.is_active):
        raise ServiceError("The content you reported no longer exists.", code="not_found", status_code=404)
    if _target_owner_id(target_type, target) == reporter.pk:
        raise ServiceError("You cannot report your own content.", code="self_report")
    field = "reported_user" if target_type == "user" else target_type
    try:
        with transaction.atomic():
            report = Report.objects.create(
                reporter=reporter,
                target_type=target_type,
                reason=reason,
                details=(details or "").strip(),
                **{field: target},
            )
    except IntegrityError:
        raise ServiceError(
            "You have already reported this. Our team is reviewing it.", code="already_reported", status_code=409
        ) from None
    _maybe_auto_hide(target_type, target)
    return report


def _maybe_auto_hide(target_type, target):
    threshold = settings.FLASHX["REPORT_AUTO_HIDE_THRESHOLD"]
    if not threshold or target_type == "user":
        return
    pending = (
        Report.objects.filter(status=Report.Status.PENDING, **{target_type: target})
        .values("reporter")
        .distinct()
        .count()
    )
    if pending >= threshold:
        type(target).objects.filter(pk=target.pk).update(is_hidden=True)


@transaction.atomic
def resolve(report_ids, moderator, *, status, note="", hide_content=False, deactivate_user=False):
    """Moderator action used by the admin. Applies to every pending report on the same targets."""
    reports = list(Report.objects.select_for_update().filter(pk__in=report_ids))
    now = timezone.now()
    for r in reports:
        target = r.target
        if target is not None:
            if hide_content and r.target_type != "user":
                type(target).objects.filter(pk=target.pk).update(is_hidden=True)
            if deactivate_user:
                owner_id = _target_owner_id(r.target_type, target)
                User.objects.filter(pk=owner_id, is_staff=False).update(is_active=False)
        Report.objects.filter(pk=r.pk).update(
            status=status, reviewed_by=moderator, reviewed_at=now, resolution_note=note
        )
    return len(reports)
