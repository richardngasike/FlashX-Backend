from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.media.models import MediaPurpose
from apps.media.services import claim_assets
from apps.notifications.models import NotificationType, TargetType
from apps.notifications.services import notify, notify_mentions, purge_target

from . import hashtags
from .models import Post, PostHashtag, PostMedia, PostTag, Visibility

User = get_user_model()


def _sync_tags(post, user_ids, actor):
    from apps.blocks.selectors import hidden_user_ids

    current = set(PostTag.objects.filter(post=post).values_list("user_id", flat=True))
    wanted = set(user_ids) - hidden_user_ids(actor)
    PostTag.objects.filter(post=post, user_id__in=current - wanted).delete()
    new_ids = wanted - current
    PostTag.objects.bulk_create([PostTag(post=post, user_id=uid) for uid in new_ids], ignore_conflicts=True)
    for user in User.objects.filter(id__in=new_ids, is_active=True):
        notify(
            recipient=user,
            sender=actor,
            notification_type=NotificationType.TAG,
            target_type=TargetType.POST,
            reference_id=post.pk,
            preview=post.caption,
        )


@transaction.atomic
def create_post(
    author,
    *,
    caption="",
    location="",
    visibility=Visibility.PUBLIC,
    category=None,
    comments_enabled=True,
    media_ids=(),
    tagged_user_ids=(),
    mood="",
    music_title="",
    event_title="",
    event_starts_at=None,
    sound_id=None,
    sound_start=0,
    sound_volume=1,
    original_volume=1,
    allow_sound_reuse=True,
):
    from apps.music import services as music

    sound = music.resolve(author, sound_id) if sound_id else None
    assets = claim_assets(author, media_ids, purposes={MediaPurpose.POST})
    post = Post.objects.create(
        author=author,
        caption=(caption or "").strip(),
        location=(location or "").strip(),
        visibility=visibility,
        category=category,
        comments_enabled=comments_enabled,
        mood=mood or "",
        music_title=((music_title or "").strip() or (sound.label if sound else ""))[:120],
        sound=sound,
        sound_start=sound_start or 0,
        sound_volume=1 if sound_volume is None else sound_volume,
        original_volume=1 if original_volume is None else original_volume,
        allow_sound_reuse=allow_sound_reuse,
        event_title=(event_title or "").strip(),
        event_starts_at=event_starts_at if (event_title or "").strip() else None,
    )
    PostMedia.objects.bulk_create(
        [
            PostMedia(
                post=post,
                asset=a,
                cloudinary_url=a.secure_url,
                cloudinary_public_id=a.public_id,
                media_type=a.resource_type,
                width=a.width,
                height=a.height,
                duration=a.duration,
                order=i,
            )
            for i, a in enumerate(assets)
        ]
    )
    hashtags.sync(post, PostHashtag, "post", "posts_count", post.caption)
    _sync_tags(post, tagged_user_ids, author)
    User.objects.filter(pk=author.pk).update(posts_count=F("posts_count") + 1)
    if sound is not None:
        music.count_use(sound)
    else:
        video = next((a for a in assets if a.resource_type == "video"), None)
        if video is not None:
            # A video without a chosen song: its soundtrack becomes an "original sound" others can use.
            original = music.create_original(
                owner=author, public_id=video.public_id, duration=video.duration, post=post
            )
            original.is_active = allow_sound_reuse and visibility == Visibility.PUBLIC
            original.uses_count = 1
            original.save(update_fields=["is_active", "uses_count"])
            post.sound = original
            post.save(update_fields=["sound"])
    if visibility != Visibility.PRIVATE:
        notify_mentions(
            text=post.caption,
            sender=author,
            target_type=TargetType.POST,
            reference_id=post.pk,
            exclude_ids=tagged_user_ids,
        )
    return post


@transaction.atomic
def update_post(post, actor, data):
    old_caption = post.caption
    for field in (
        "caption",
        "location",
        "visibility",
        "category",
        "comments_enabled",
        "mood",
        "music_title",
        "event_title",
        "event_starts_at",
        "sound_start",
        "sound_volume",
        "original_volume",
        "allow_sound_reuse",
    ):
        if field in data:
            value = data[field]
            setattr(post, field, value.strip() if isinstance(value, str) else value)
    if "sound_id" in data:
        _change_sound(post, actor, data["sound_id"])
    post.save()
    _sync_original_sound(post)
    if post.caption != old_caption:
        hashtags.sync(post, PostHashtag, "post", "posts_count", post.caption)
    if "tagged_user_ids" in data:
        _sync_tags(post, data["tagged_user_ids"], actor)
    return post


def _change_sound(obj, actor, sound_id):
    """Swap or remove the sound on a post or reel, keeping use counts right."""
    from apps.music import services as music

    origin = getattr(obj, "original_sound", None) if obj.pk else None
    new = music.resolve(actor, sound_id) if sound_id else None
    if new is None and origin is not None:
        new = origin  # removing a song falls back to the video's own audio
    if (new.pk if new else None) == obj.sound_id:
        return
    if obj.sound_id and (origin is None or obj.sound_id != origin.pk):
        music.release_use(obj.sound_id)
    obj.sound = new
    if new is not None and (origin is None or new.pk != origin.pk):
        music.count_use(new)
    if hasattr(obj, "music_title"):
        obj.music_title = new.label if new is not None and new != origin else ""


def _sync_original_sound(post):
    origin = getattr(post, "original_sound", None)
    if origin is not None:
        active = post.allow_sound_reuse and post.visibility == Visibility.PUBLIC and not post.is_hidden
        if origin.is_active != active:
            origin.is_active = active
            origin.save(update_fields=["is_active"])


@transaction.atomic
def delete_post(post):
    from apps.comments.models import Comment

    comment_ids = list(Comment.objects.filter(post=post).values_list("id", flat=True))
    hashtags.release(post, PostHashtag, "post", "posts_count")
    purge_target(TargetType.POST, [post.pk])
    purge_target(TargetType.COMMENT, comment_ids)
    User.objects.filter(pk=post.author_id).update(posts_count=Greatest(F("posts_count") - 1, 0))
    origin = getattr(post, "original_sound", None)
    if post.sound_id and (origin is None or post.sound_id != origin.pk):
        from apps.music.services import release_use

        release_use(post.sound_id)
    post.delete()  # PostMedia rows cascade; their assets are removed from Cloudinary.
