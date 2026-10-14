from django.urls import path

from . import views

urlpatterns = [
    path("", views.WalletView.as_view(), name="wallet"),
    path("transactions/", views.TransactionsView.as_view(), name="wallet-transactions"),
    path("gift-history/", views.GiftHistoryView.as_view(), name="wallet-gift-history"),
    path("gifts/", views.GiftCatalogView.as_view(), name="wallet-gifts"),
    path("recharge/", views.RechargeOptionsView.as_view(), name="wallet-recharge"),
    path("transfer/", views.TransferView.as_view(), name="wallet-transfer"),
]
