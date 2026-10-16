from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("password/change/", views.ChangePasswordView.as_view(), name="auth-password-change"),
    path("password/reset/", views.PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("password/reset/confirm/", views.PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
    path("password/reset/code/", views.PasswordResetCodeView.as_view(), name="auth-password-reset-code"),
    path(
        "password/reset/code/verify/",
        views.PasswordResetCodeVerifyView.as_view(),
        name="auth-password-reset-code-verify",
    ),
    path("email/verify/", views.EmailVerifyView.as_view(), name="auth-email-verify"),
    path("email/resend/", views.EmailResendView.as_view(), name="auth-email-resend"),
    path("email/change/", views.EmailChangeView.as_view(), name="auth-email-change"),
    path("username-available/", views.UsernameAvailabilityView.as_view(), name="auth-username-available"),
]
