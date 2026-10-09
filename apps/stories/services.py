import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.exceptions import ServiceError
from apps.media.models import MediaPurpose
from apps.media.services import claim_one
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify, purge_target

from .models import Story, StoryReaction, StoryView

logger = logging.getLogger(__name__)


@transaction.atomic
def create_story(author, *, media_id, caption=""):
    asset = claim_one(author, media_id, purposes={MediaPurpose.STORY})
    return Story.objects.create(
        author=author,
        asset=asset,
        cloudinary_url=asset.secure_url,
        cloudinary_public_id=asset.public_id,
        media_type=asset.resource_type,
        caption=(caption or "").strip(),
        duration=asset.duration,
        width=asset.width,
        height=asset.height,
    )


def mark_viewed(story, viewer) -> bool:
    if story.author_id == viewer.pk:
        return False
    try:
        with transaction.atomic():
            StoryView.objects.create(story=story, viewer=viewer)
            Story.objects.filter(pk=story.pk).update(views_count=F("views_count") + 1)
        return True
    except IntegrityError:
        return False


@transaction.atomic
def react(story, user, reaction):
    if story.author_id == user.pk:
        raise ServiceError("You cannot react to your own story.", code="self_reaction")
    obj, created = StoryReaction.objects.update_or_create(story=story, user=user, defaults={"reaction": reaction})
    if created:
        notify(
            recipient=story.author,
            sender=user,
            notification_type=NotificationType.STORY_REACTION,
            target_type=TargetType.STORY,
            reference_id=story.pk,
            preview=reaction,
        )
    mark_viewed(story, user)
    return obj


@transaction.atomic
def unreact(story, user):
    StoryReaction.objects.filter(story=story, user=user).delete()
    from apps.notifications.services import withdraw

    withdraw(
        sender=user,
        notification_type=NotificationType.STORY_REACTION,
        target_type=TargetType.STORY,
        reference_id=story.pk,
    )


def reply(story, user, content):
    from apps.messaging import services as messaging

    if story.author_id == user.pk:
        raise ServiceError("You cannot reply to your own story.", code="self_reply")
    convo = messaging.get_or_create_direct(user, story.author)
    message = messaging.send_message(
        user, convo, content=content, story=story, notification_type=NotificationType.STORY_REPLY
    )
    mark_viewed(story, user)
    return message


@transaction.atomic
def delete_story(story):
    purge_target(TargetType.STORY, [story.pk])
    story.delete()  # Asset removed from Cloudinary via signal.


def purge_expired(batch_size=500) -> int:
    """Delete expired stories (and their Cloudinary files). Run from cron."""
    total = 0
    while True:
        ids = list(Story.objects.filter(expires_at__lte=timezone.now()).values_list("id", flat=True)[:batch_size])
        if not ids:
            return total
        with transaction.atomic():
            purge_target(TargetType.STORY, ids)
            for story in Story.objects.filter(id__in=ids):
                story.delete()
        total += len(ids)
