from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import FeedCursorPagination
from apps.media import cloudinary_service as cld

from .models import DeviceToken, Notification
from .serializers import DeviceTokenDeleteSerializer, DeviceTokenSerializer, NotificationSerializer


def resolve_targets(notifications):
    """Map (target_type, reference_id) -> {route ids, thumbnail} with a few bulk queries."""
    from apps.comments.models import Comment
    from apps.posts.models import PostMedia
    from apps.reels.models import Reel
    from apps.stories.models import Story

    by_type = {}
    for n in notifications:
        if n.reference_id.isdigit():
            by_type.setdefault(n.target_type, set()).add(int(n.reference_id))

    out = {}
    comment_post = {}
    if by_type.get("comment"):
        for c in Comment.objects.filter(pk__in=by_type["comment"]).only("id", "post_id", "reel_id"):
            comment_post[c.pk] = (c.post_id, c.reel_id)
            if c.post_id:
                by_type.setdefault("post", set()).add(c.post_id)
            if c.reel_id:
                by_type.setdefault("reel", set()).add(c.reel_id)

    post_thumbs = {}
    for m in PostMedia.objects.filter(post_id__in=by_type.get("post", ()), order=0):
        post_thumbs[m.post_id] = (cld.variants(m.cloudinary_public_id, m.media_type) or {}).get("thumbnail")
    reel_thumbs = {
        r.pk: cld.video_poster_url(r.cloudinary_public_id, width=240)
        for r in Reel.objects.filter(pk__in=by_type.get("reel", ())).only("id", "cloudinary_public_id")
    }
    story_thumbs = {
        s.pk: (cld.variants(s.cloudinary_public_id, s.media_type) or {}).get("thumbnail")
        for s in Story.objects.filter(pk__in=by_type.get("story", ()))
    }

    for pid in by_type.get("post", ()):
        out[("post", str(pid))] = {"post_id": pid, "thumbnail": post_thumbs.get(pid)}
    for rid in by_type.get("reel", ()):
        out[("reel", str(rid))] = {"reel_id": rid, "thumbnail": reel_thumbs.get(rid)}
    for sid in by_type.get("story", ()):
        out[("story", str(sid))] = {
            "story_id": sid,
            "thumbnail": story_thumbs.get(sid),
            "available": sid in story_thumbs,
        }
    for cid, (post_id, reel_id) in comment_post.items():
        out[("comment", str(cid))] = {
            "comment_id": cid,
            "post_id": post_id,
            "reel_id": reel_id,
            "thumbnail": post_thumbs.get(post_id) if post_id else reel_thumbs.get(reel_id),
        }
    return out


class NotificationListView(APIView):
    serializer_class = NotificationSerializer

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user, sender__is_active=True).select_related(
            "sender__profile_image"
        )
        if request.query_params.get("unread") == "true":
            qs = qs.filter(is_read=False)
        if request.query_params.get("type"):
            qs = qs.filter(notification_type__in=request.query_params["type"].split(","))
        else:
            # Message notifications live on the Messages screen badge, not the activity feed.
            qs = qs.exclude(notification_type="message")
        paginator = FeedCursorPagination()
        paginator.page_size = 25
        page = paginator.paginate_queryset(qs, request, view=self)
        from apps.follows.models import Follow

        sender_ids = {n.sender_id for n in page if n.sender_id}
        following = set(
            Follow.objects.filter(follower=request.user, following_id__in=sender_ids).values_list(
                "following_id", flat=True
            )
        )
        ctx = {"request": request, "targets": resolve_targets(page), "following_ids": following}
        return paginator.get_paginated_response(NotificationSerializer(page, many=True, context=ctx).data)


class UnreadCountView(APIView):
    serializer_class = NotificationSerializer

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user, is_read=False)
        return Response(
            {
                "notifications": qs.exclude(notification_type="message").count(),
                "messages": qs.filter(notification_type="message").count(),
            }
        )


class MarkReadView(APIView):
    serializer_class = NotificationSerializer

    def post(self, request, pk):
        updated = Notification.objects.filter(recipient=request.user, pk=pk).update(is_read=True)
        if not updated:
            raise NotFound("Notification not found.")
        return Response({"is_read": True})


class MarkAllReadView(APIView):
    serializer_class = NotificationSerializer

    def post(self, request):
        updated = (
            Notification.objects.filter(recipient=request.user, is_read=False)
            .exclude(notification_type="message")
            .update(is_read=True)
        )
        return Response({"marked_read": updated})


class NotificationDeleteView(APIView):
    serializer_class = NotificationSerializer

    def delete(self, request, pk):
        Notification.objects.filter(recipient=request.user, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceTokenView(APIView):
    """
    POST registers this phone for push notifications (call after login and
    whenever Firebase issues a new token). DELETE unregisters it (call before
    logout). A token moves to whoever registered it last.
    """

    serializer_class = DeviceTokenSerializer

    def post(self, request):
        s = DeviceTokenSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        device, created = DeviceToken.objects.update_or_create(
            token=s.validated_data["token"],
            defaults={
                "user": request.user,
                "platform": s.validated_data["platform"],
                "app_version": s.validated_data["app_version"],
            },
        )
        return Response(
            {"id": device.pk, "platform": device.platform},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        s = DeviceTokenDeleteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        DeviceToken.objects.filter(user=request.user, token=s.validated_data["token"]).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestPushView(APIView):
    """
    POST /api/notifications/devices/test/ — sends a test notification to your
    own phones and reports the outcome, to check push end to end.
    """

    serializer_class = DeviceTokenSerializer

    def post(self, request):
        from . import push

        result = push.send_to_user_detailed(
            request.user.pk,
            title="FlashX",
            body="Notifications are working on this phone.",
            data={"type": "test", "target_type": "test", "reference_id": ""},
            channel=push.CHANNEL_MESSAGES,
            tag="flashx-test",
            badge=push.unread_badge(request.user.pk),
            high_priority=True,
        )
        if not result["enabled"]:
            message = "Push is not configured on the server (FCM_SERVICE_ACCOUNT_JSON is empty)."
        elif result["devices"] == 0:
            message = "This account has no registered phones. Open the app, sign in and allow notifications."
        elif result["sent"]:
            message = f"Sent to {result['sent']} of {result['devices']} phone(s)."
        else:
            message = "Firebase did not accept the notification: " + "; ".join(result["errors"][:3])
        return Response({**result, "message": message})
