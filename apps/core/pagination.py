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
