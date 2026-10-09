from django.urls import path

from . import views

urlpatterns = [
    path("", views.ReportView.as_view(), name="reports"),
    path("reasons/", views.ReportReasonsView.as_view(), name="report-reasons"),
]
