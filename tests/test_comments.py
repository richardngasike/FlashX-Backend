from django.urls import reverse

from apps.notifications.models import Notification
from apps.posts.models import Post

from .base import FlashXTestCase


class CommentTests(FlashXTestCase):
    def setUp(self):
        super().setUp()
        self.author = self.make_user("author")
        self.post = self.make_post(self.author)
        self.me = self.auth(self.make_user("commenter"))

    def comment(self, text="Nice!", parent=None):
        data = {"content": text}
        if parent:
            data["parent_id"] = parent
        return self.client.post(reverse("posts-comments", args=[self.post.id]), data)

    def test_comment_and_reply_threading(self):
        root = self.assertOk(self.comment("First"), 201)
        reply = self.assertOk(self.comment("Reply", root["id"]), 201)
        self.assertEqual(reply["parent_id"], root["id"])
        # Reply to a reply attaches to the root (one level deep)
        nested = self.assertOk(self.comment("Nested", reply["id"]), 201)
        self.assertEqual(nested["parent_id"], root["id"])
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 3)

        top = self.assertOk(self.client.get(reverse("posts-comments", args=[self.post.id])))["results"]
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["replies_count"], 2)
        replies = self.assertOk(self.client.get(reverse("comments-replies", args=[root["id"]])))["results"]
        self.assertEqual([r["content"] for r in replies], ["Reply", "Nested"])
        self.assertTrue(Notification.objects.filter(recipient=self.author, notification_type="comment").exists())

    def test_spec_alias_endpoint(self):
        self.assertOk(self.client.post(reverse("posts-comment", args=[self.post.id]), {"content": "alias"}), 201)

    def test_empty_comment_and_disabled_comments(self):
        self.assertError(self.comment("   "), 400)
        Post.objects.filter(pk=self.post.pk).update(comments_enabled=False)
        self.assertError(self.comment("hi"), 403, "comments_disabled")

    def test_delete_permissions_and_counter(self):
        root = self.assertOk(self.comment("mine"), 201)
        self.assertOk(self.comment("reply", root["id"]), 201)
        stranger = self.make_user()
        self.auth(stranger)
        self.assertError(self.client.delete(reverse("comments-detail", args=[root["id"]])), 403)
        # Post author can moderate comments on their own post
        self.auth(self.author)
        self.assertOk(self.client.delete(reverse("comments-detail", args=[root["id"]])), 204)
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 0)

    def test_like_comment(self):
        c = self.assertOk(self.comment("like me"), 201)
        self.auth(self.author)
        self.assertEqual(self.assertOk(self.client.post(reverse("comments-like", args=[c["id"]])))["likes_count"], 1)
        self.assertTrue(Notification.objects.filter(recipient=self.me, notification_type="comment_like").exists())

    def test_mentions_in_comment(self):
        tagged = self.make_user("pal")
        self.assertOk(self.comment("hey @pal look"), 201)
        self.assertTrue(Notification.objects.filter(recipient=tagged, notification_type="mention").exists())
