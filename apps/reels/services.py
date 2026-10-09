from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.media.models import MediaPurpose
from apps.media.services import claim_one
from apps.notifications.models import TargetType
from apps.notifications.services import notify_mentions, purge_target
from apps.posts import hashtags

from .models import Reel, ReelHashtag, ReelView

User = get_user_model()


@transaction.atomic
def create_reel(author, *, media_id, caption="", audio_title="", comments_enabled=True):
    asset = claim_one(author, media_id, purposes={MediaPurpose.REEL})
    reel = Reel.objects.create(
        author=author,
        asset=asset,
        video_url=asset.secure_url,
        cloudinary_public_id=asset.public_id,
        caption=(caption or "").strip(),
        audio_title=(audio_title or "").strip(),
        duration=asset.duration,
        width=asset.width,
        height=asset.height,
        comments_enabled=comments_enabled,
    )
    hashtags.sync(reel, ReelHashtag, "reel", "reels_count", reel.caption)
    User.objects.filter(pk=author.pk).update(reels_count=F("reels_count") + 1)
    notify_mentions(text=reel.caption, sender=author, target_type=TargetType.REEL, reference_id=reel.pk)
    return reel


@transaction.atomic
def update_reel(reel, data):
    old = reel.caption
    for field in ("caption", "audio_title", "comments_enabled"):
        if field in data:
            value = data[field]
            setattr(reel, field, value.strip() if isinstance(value, str) else value)
    reel.save()
    if reel.caption != old:
        hashtags.sync(reel, ReelHashtag, "reel", "reels_count", reel.caption)
    return reel


@transaction.atomic
def delete_reel(reel):
    from apps.comments.models import Comment

    hashtags.release(reel, ReelHashtag, "reel", "reels_count")
    purge_target(TargetType.REEL, [reel.pk])
    purge_target(TargetType.COMMENT, list(Comment.objects.filter(reel=reel).values_list("id", flat=True)))
    User.objects.filter(pk=reel.author_id).update(reels_count=Greatest(F("reels_count") - 1, 0))
    reel.delete()


def record_view(reel, user, watched_seconds=0) -> int:
    """Counts one view per user; later calls only extend watch time."""
    try:
        with transaction.atomic():
            ReelView.objects.create(reel=reel, user=user, watched_seconds=watched_seconds)
            Reel.objects.filter(pk=reel.pk).update(views=F("views") + 1)
    except IntegrityError:
        ReelView.objects.filter(reel=reel, user=user, watched_seconds__lt=watched_seconds).update(
            watched_seconds=watched_seconds
        )
    return Reel.objects.filter(pk=reel.pk).values_list("views", flat=True).first() or 0
