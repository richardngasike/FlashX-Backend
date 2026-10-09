from .models import Notification


def notify(*, recipient, sender, notification_type, target_type, reference_id, preview=""):
    if recipient is None or sender is None:
        return None
    if recipient.pk == sender.pk or not recipient.is_active:
        return None
    return Notification.objects.create(
        recipient=recipient,
        sender=sender,
        notification_type=notification_type,
        target_type=target_type,
        reference_id=str(reference_id),
        preview=(preview or "")[:160],
    )


def withdraw(*, sender, notification_type, target_type, reference_id, recipient=None):
    """Remove a notification whose cause was undone (unlike, unfollow, deleted comment)."""
    qs = Notification.objects.filter(
        sender=sender, notification_type=notification_type, target_type=target_type, reference_id=str(reference_id)
    )
    if recipient is not None:
        qs = qs.filter(recipient=recipient)
    qs.delete()


def purge_target(target_type, reference_ids):
    Notification.objects.filter(target_type=target_type, reference_id__in=[str(r) for r in reference_ids]).delete()


def notify_mentions(*, text, sender, target_type, reference_id, exclude_ids=()):
    """Notify every active user @mentioned in ``text``."""
    from django.contrib.auth import get_user_model

    from apps.core.text import extract_mentions

    from .models import NotificationType

    names = extract_mentions(text)
    if not names:
        return
    User = get_user_model()
    from django.db.models import Q

    query = Q()
    for n in names:
        query |= Q(username__iexact=n)
    for user in User.objects.filter(query, is_active=True).exclude(pk__in=list(exclude_ids)):
        notify(
            recipient=user,
            sender=sender,
            notification_type=NotificationType.MENTION,
            target_type=target_type,
            reference_id=reference_id,
            preview=text,
        )
