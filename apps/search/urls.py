from django.urls import path

from . import views

search_urlpatterns = [
    path("", views.SearchView.as_view(), name="search"),
    path("recent/", views.RecentSearchView.as_view(), name="search-recent"),
    path("recent/<int:pk>/", views.RecentSearchItemView.as_view(), name="search-recent-item"),
]

explore_urlpatterns = [
    path("", views.ExploreView.as_view(), name="explore"),
    path("overview/", views.ExploreOverviewView.as_view(), name="explore-overview"),
    path("categories/", views.CategoryListView.as_view(), name="explore-categories"),
    path("hashtags/<str:name>/", views.HashtagDetailView.as_view(), name="explore-hashtag"),
]
