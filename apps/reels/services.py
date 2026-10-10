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
def create_reel(
    author,
    *,
    media_id,
    caption="",
    audio_title="",
    comments_enabled=True,
    sound_id=None,
    sound_start=0,
    sound_volume=1,
    original_volume=1,
    allow_sound_reuse=True,
):
    from apps.music import services as music

    sound = music.resolve(author, sound_id) if sound_id else None
    asset = claim_one(author, media_id, purposes={MediaPurpose.REEL})
    reel = Reel.objects.create(
        author=author,
        asset=asset,
        video_url=asset.secure_url,
        cloudinary_public_id=asset.public_id,
        caption=(caption or "").strip(),
        audio_title=((audio_title or "").strip() or (sound.label if sound else ""))[:120],
        sound=sound,
        sound_start=sound_start or 0,
        sound_volume=1 if sound_volume is None else sound_volume,
        original_volume=1 if original_volume is None else original_volume,
        allow_sound_reuse=allow_sound_reuse,
        duration=asset.duration,
        width=asset.width,
        height=asset.height,
        comments_enabled=comments_enabled,
    )
    hashtags.sync(reel, ReelHashtag, "reel", "reels_count", reel.caption)
    User.objects.filter(pk=author.pk).update(reels_count=F("reels_count") + 1)
    if sound is not None:
        music.count_use(sound)
    else:
        original = music.create_original(owner=author, public_id=asset.public_id, duration=asset.duration, reel=reel)
        original.is_active = allow_sound_reuse
        original.uses_count = 1
        original.save(update_fields=["is_active", "uses_count"])
        reel.sound = original
        reel.save(update_fields=["sound"])
    notify_mentions(text=reel.caption, sender=author, target_type=TargetType.REEL, reference_id=reel.pk)
    return reel


@transaction.atomic
def update_reel(reel, data, actor=None):
    old = reel.caption
    for field in (
        "caption",
        "audio_title",
        "comments_enabled",
        "sound_start",
        "sound_volume",
        "original_volume",
        "allow_sound_reuse",
    ):
        if field in data:
            value = data[field]
            setattr(reel, field, value.strip() if isinstance(value, str) else value)
    if "sound_id" in data:
        from apps.posts.services import _change_sound

        _change_sound(reel, actor or reel.author, data["sound_id"])
        if reel.sound is not None and "audio_title" not in data:
            reel.audio_title = reel.sound.label
    reel.save()
    origin = getattr(reel, "original_sound", None)
    if origin is not None and origin.is_active != (reel.allow_sound_reuse and not reel.is_hidden):
        origin.is_active = reel.allow_sound_reuse and not reel.is_hidden
        origin.save(update_fields=["is_active"])
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
    origin = getattr(reel, "original_sound", None)
    if reel.sound_id and (origin is None or reel.sound_id != origin.pk):
        from apps.music.services import release_use

        release_use(reel.sound_id)
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
