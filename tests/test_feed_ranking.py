from datetime import timedelta

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.blocks.models import Block
from apps.follows.models import Follow
from apps.likes.models import Like
from apps.posts.models import Post
from apps.posts.ranking import _spread

from .base import FlashXTestCase


class RankedFeedTests(FlashXTestCase):
    """The For you home feed: followed people plus discovery, reshuffled on refresh."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.me = self.auth(self.make_user("me"))
        self.friend = self.make_user("friend")
        self.stranger = self.make_user("stranger")
        Follow.objects.create(follower=self.me, following=self.friend)

    def feed(self, **params):
        return self.assertOk(self.client.get(reverse("posts-feed"), params))

    def ids(self, **params):
        return [p["id"] for p in self.feed(**params)["results"]]

    def test_includes_followed_and_public_discovery(self):
        mine = self.make_post(self.friend, "friend post")
        discovered = self.make_post(self.stranger, "public post")
        ids = self.ids(seed="a")
        self.assertIn(mine.id, ids)
        self.assertIn(discovered.id, ids)
        # The following-only feed still excludes strangers.
        self.assertNotIn(discovered.id, self.ids(feed="following"))

    def test_private_followers_only_and_blocked_are_excluded(self):
        private_user = self.make_user("private", is_private=True)
        hidden_private = self.make_post(private_user, "private account")
        followers_only = self.make_post(self.stranger, "fo", visibility="followers")
        blocker = self.make_user("blocker")
        blocked_post = self.make_post(blocker, "blocked")
        Block.objects.create(blocker=blocker, blocked=self.me)
        ids = self.ids(seed="b")
        for p in (hidden_private, followers_only, blocked_post):
            self.assertNotIn(p.id, ids)

    def test_followed_beats_equal_stranger_post(self):
        followed = self.make_post(self.friend, "f")
        stranger = self.make_post(self.stranger, "s")
        # Over several refreshes the followed post is ahead every time.
        for seed in "abcdef":
            cache.clear()
            ids = self.ids(seed=seed)
            self.assertLess(ids.index(followed.id), ids.index(stranger.id))

    def test_own_new_post_is_first(self):
        for i in range(5):
            self.make_post(self.friend, f"f{i}")
        own = self.make_post(self.me, "just posted")
        self.assertEqual(self.ids(seed="z")[0], own.id)

    def test_refresh_with_new_seed_changes_order_and_same_seed_pages_stably(self):
        authors = [self.make_user(f"creator{i}") for i in range(9)]
        for i, a in enumerate(authors):
            for j in range(3):
                self.make_post(a, f"{i}-{j}")
        first = self.ids(seed="one", page_size=30)
        self.assertEqual(first, self.ids(seed="one", page_size=30))
        second = self.ids(seed="two", page_size=30)
        self.assertEqual(sorted(first), sorted(second))
        self.assertNotEqual(first, second)
        # Paging with the same seed returns each post once.
        page1 = self.feed(seed="three", page_size=10)
        page2 = self.assertOk(self.client.get(page1["next"]))
        both = [p["id"] for p in page1["results"]] + [p["id"] for p in page2["results"]]
        self.assertEqual(len(both), len(set(both)))

    def test_engagement_and_freshness_matter(self):
        old = self.make_post(self.stranger, "old")
        Post.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        popular = self.make_post(self.make_user("pop"), "popular")
        Post.objects.filter(pk=popular.pk).update(likes_count=200, comments_count=40)
        ids = self.ids(seed="q")
        self.assertLess(ids.index(popular.id), ids.index(old.id))

    def test_already_liked_steps_back(self):
        a = self.make_post(self.make_user("a1"), "a")
        b = self.make_post(self.make_user("b1"), "b")
        Like.objects.create(user=self.me, post=a)
        Post.objects.filter(pk__in=[a.pk, b.pk]).update(likes_count=1)
        wins = 0
        for seed in "abcdefgh":
            cache.clear()
            ids = self.ids(seed=seed)
            wins += ids.index(b.id) < ids.index(a.id)
        self.assertGreaterEqual(wins, 6)

    def test_spread_keeps_authors_apart(self):
        ranked = [(1, "x"), (2, "x"), (3, "x"), (4, "y"), (5, "z")]
        self.assertEqual(_spread(ranked), [1, 4, 5, 2, 3])
