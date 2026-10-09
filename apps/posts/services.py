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
    current = set(PostTag.objects.filter(post=post).values_list("user_id", flat=True))
    wanted = set(user_ids)
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
):
    assets = claim_assets(author, media_ids, purposes={MediaPurpose.POST})
    post = Post.objects.create(
        author=author,
        caption=(caption or "").strip(),
        location=(location or "").strip(),
        visibility=visibility,
        category=category,
        comments_enabled=comments_enabled,
        mood=mood or "",
        music_title=(music_title or "").strip(),
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
    ):
        if field in data:
            value = data[field]
            setattr(post, field, value.strip() if isinstance(value, str) else value)
    post.save()
    if post.caption != old_caption:
        hashtags.sync(post, PostHashtag, "post", "posts_count", post.caption)
    if "tagged_user_ids" in data:
        _sync_tags(post, data["tagged_user_ids"], actor)
    return post


@transaction.atomic
def delete_post(post):
    from apps.comments.models import Comment

    comment_ids = list(Comment.objects.filter(post=post).values_list("id", flat=True))
    hashtags.release(post, PostHashtag, "post", "posts_count")
    purge_target(TargetType.POST, [post.pk])
    purge_target(TargetType.COMMENT, comment_ids)
    User.objects.filter(pk=post.author_id).update(posts_count=Greatest(F("posts_count") - 1, 0))
    post.delete()  # PostMedia rows cascade; their assets are removed from Cloudinary.
