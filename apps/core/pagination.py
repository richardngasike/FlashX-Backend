from rest_framework.pagination import CursorPagination, LimitOffsetPagination, PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50

    def get_paginated_response(self, data):
        return Response(
            {
                "results": data,
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "page": self.page.number,
                "total_pages": self.page.paginator.num_pages,
            }
        )


class FeedCursorPagination(CursorPagination):
    """Stable, insert-safe pagination for infinite-scroll feeds."""

    page_size = 15
    page_size_query_param = "page_size"
    max_page_size = 50
    ordering = ("-created_at", "-id")

    def get_paginated_response(self, data):
        return Response({"results": data, "next": self.get_next_link(), "previous": self.get_previous_link()})


class ChatCursorPagination(FeedCursorPagination):
    page_size = 30
    max_page_size = 100


class SearchPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 50

    def get_paginated_response(self, data):
        return Response(
            {"results": data, "count": self.count, "next": self.get_next_link(), "previous": self.get_previous_link()}
        )


class RankedFeedPagination(LimitOffsetPagination):
    """
    Offset pages for ranked feeds whose order is not a column (For You).
    Same response shape as the cursor feeds, so the app just follows ``next``.
    """

    default_limit = 8
    max_limit = 30

    def get_limit(self, request):
        # Accept the cursor feeds' page_size too.
        if "page_size" in request.query_params and self.limit_query_param not in request.query_params:
            try:
                return max(1, min(int(request.query_params["page_size"]), self.max_limit))
            except ValueError:
                return self.default_limit
        return super().get_limit(request)

    def get_paginated_response(self, data):
        return Response({"results": data, "next": self.get_next_link(), "previous": self.get_previous_link()})
