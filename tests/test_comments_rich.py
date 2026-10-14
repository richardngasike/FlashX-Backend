from django.core.cache import cache
from django.urls import reverse

from apps.comments.models import Comment
from apps.posts.models import Post

from .base import FlashXTestCase

GIF = "https://media.giphy.com/media/abc123/giphy.gif"


class RichCommentTests(FlashXTestCase):
    """GIF comments, editing, pinning, sorting, and reading lists without using the posting quota."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.author = self.make_user("creator")
        self.post = self.make_post(self.author)
        self.me = self.auth(self.make_user("fan"))

    def url(self):
        return reverse("posts-comments", args=[self.post.id])

    def comment(self, **data):
        return self.client.post(self.url(), data, format="json")

    def test_gif_comment_with_or_without_text(self):
        c = self.assertOk(self.comment(gif_url=GIF, gif_width=480, gif_height=270), 201)
        self.assertEqual((c["content"], c["gif"]), ("", {"url": GIF, "width": 480, "height": 270}))
        both = self.assertOk(self.comment(content="lol", gif_url=GIF), 201)
        self.assertEqual(both["content"], "lol")
        self.assertError(self.comment(gif_url="https://evil.example.com/x.gif"), 400, "invalid_gif")
        self.assertError(self.comment(content="  "), 400)
        self.assertEqual(Post.objects.get(pk=self.post.pk).comments_count, 2)

    def test_library_gif_own_or_public_only(self):
        from apps.gifs.models import Gif

        mine = Gif.objects.create(
            owner=self.me,
            source="upload",
            public_id="flashx/u/gif/a",
            url="https://res.cloudinary.com/x/image/upload/a.gif",
        )
        theirs = Gif.objects.create(
            owner=self.author,
            source="upload",
            public_id="flashx/u/gif/b",
            url="https://res.cloudinary.com/x/image/upload/b.gif",
        )
        self.assertOk(self.comment(gif_url=mine.url), 201)
        self.assertError(self.comment(gif_url=theirs.url), 400, "invalid_gif")
        Gif.objects.filter(pk=theirs.pk).update(is_public=True)
        self.assertOk(self.comment(gif_url=theirs.url), 201)
        self.assertEqual(Gif.objects.get(pk=theirs.pk).uses_count, 1)

    def test_edit_own_comment_only(self):
        c = self.assertOk(self.comment(content="helo"), 201)
        self.assertFalse(c["is_edited"])
        self.assertTrue(c["can_edit"])
        edited = self.assertOk(
            self.client.patch(reverse("comments-detail", args=[c["id"]]), {"content": "hello"}, format="json")
        )
        self.assertEqual((edited["content"], edited["is_edited"]), ("hello", True))
        self.assertError(
            self.client.patch(reverse("comments-detail", args=[c["id"]]), {"content": ""}, format="json"), 400
        )
        self.auth(self.author)
        self.assertError(
            self.client.patch(reverse("comments-detail", args=[c["id"]]), {"content": "x"}, format="json"),
            403,
            "permission_denied",
        )

    def test_author_pins_one_comment_shown_first(self):
        first = self.assertOk(self.comment(content="first"), 201)
        second = self.assertOk(self.comment(content="second"), 201)
        reply = self.assertOk(self.comment(content="reply", parent_id=first["id"]), 201)
        self.assertError(self.client.post(reverse("comments-pin", args=[second["id"]])), 403, "permission_denied")
        self.auth(self.author)
        pinned = self.assertOk(self.client.post(reverse("comments-pin", args=[second["id"]])))
        self.assertTrue(pinned["is_pinned"])
        self.assertError(self.client.post(reverse("comments-pin", args=[reply["id"]])), 400)
        ids = [c["id"] for c in self.assertOk(self.client.get(self.url()))["results"]]
        self.assertEqual(ids, [second["id"], first["id"]])
        # Pinning another replaces it.
        self.assertOk(self.client.post(reverse("comments-pin", args=[first["id"]])))
        self.assertEqual(list(Comment.objects.filter(is_pinned=True).values_list("id", flat=True)), [first["id"]])
        self.assertOk(self.client.delete(reverse("comments-pin", args=[first["id"]])))
        self.assertFalse(Comment.objects.filter(is_pinned=True).exists())

    def test_sorting(self):
        a = self.assertOk(self.comment(content="a"), 201)
        b = self.assertOk(self.comment(content="b"), 201)
        c = self.assertOk(self.comment(content="c"), 201)
        Comment.objects.filter(pk=b["id"]).update(likes_count=5)
        get = lambda sort: [x["id"] for x in self.assertOk(self.client.get(self.url(), {"sort": sort}))["results"]]  # noqa: E731
        self.assertEqual(get("oldest"), [a["id"], b["id"], c["id"]])
        self.assertEqual(get("newest"), [c["id"], b["id"], a["id"]])
        self.assertEqual(get("top"), [b["id"], a["id"], c["id"]])

    def test_reading_comments_does_not_use_the_posting_quota(self):
        from unittest import mock

        from apps.core.throttles import ContentCreateThrottle

        with mock.patch.object(ContentCreateThrottle, "rate", "2/hour", create=True):
            for _ in range(5):
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertOk(self.comment(content="one"), 201)
            self.assertOk(self.comment(content="two"), 201)
            self.assertEqual(self.comment(content="three").status_code, 429)
