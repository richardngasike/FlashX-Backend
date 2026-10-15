from django.urls import path

from . import views

urlpatterns = [
    path("verification/", views.VerificationView.as_view(), name="payments-verification"),
    path("<int:pk>/", views.PaymentDetailView.as_view(), name="payments-detail"),
    path("mpesa/callback/<str:token>/", views.MpesaCallbackView.as_view(), name="payments-mpesa-callback"),
]
