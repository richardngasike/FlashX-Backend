from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.blocks.models import Block
from apps.blocks.selectors import blocking_me
from apps.core.exceptions import ServiceError
from apps.core.pagination import StandardPagination
from apps.core.throttles import (
    AuthRateThrottle,
    LookupThrottle,
    PasswordResetThrottle,
    RegisterThrottle,
    TokenRefreshThrottle,
)
from apps.follows import services as follow_services
from apps.follows.models import Follow, FollowRequest

from . import selectors, services
from .serializers import (
    ChangePasswordSerializer,
    DeleteAccountSerializer,
    EmailChangeSerializer,
    EmailCodeSerializer,
    FlashXTokenRefreshSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordResetCodeSerializer,
    PasswordResetCodeVerifySerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PresenceSerializer,
    RegisterSerializer,
    SuggestedUserSerializer,
    UpdateMeSerializer,
    UserListSerializer,
    UserProfileSerializer,
)
from .validators import validate_username

User = get_user_model()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class RegisterView(APIView):
    serializer_class = RegisterSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        # The app shows the code screen next; nothing else works until it is entered.
        services.send_email_code(user)
        user = selectors.with_follow_flags(selectors.base_users(), user).get(pk=user.pk)
        return Response(
            {"user": MeSerializer(user, context={"request": request}).data, "tokens": services.issue_tokens(user)},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    serializer_class = LoginSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        identifier = s.validated_data["identifier"]
        # Many wrong passwords for one account, from any number of addresses: pause it.
        if services.login_locked(identifier):
            raise ServiceError(
                "Too many attempts. Try again in a few minutes or reset your password.",
                code="throttled",
                status_code=429,
            )
        user = services.authenticate_identifier(identifier, s.validated_data["password"])
        if user is None:
            services.record_login_failure(identifier)
            # Explicit 401: the view has no authenticators, so AuthenticationFailed would become 403.
            raise ServiceError("Invalid username/email or password.", code="invalid_credentials", status_code=401)
        services.clear_login_failures(identifier)
        from django.contrib.auth.models import update_last_login

        update_last_login(None, user)
        user = selectors.with_follow_flags(selectors.base_users(), user).get(pk=user.pk)
        return Response(
            {"user": MeSerializer(user, context={"request": request}).data, "tokens": services.issue_tokens(user)}
        )


class RefreshView(TokenRefreshView):
    serializer_class = FlashXTokenRefreshSerializer
    throttle_classes = [TokenRefreshThrottle]


class LogoutView(APIView):
    serializer_class = LogoutSerializer
    allow_unverified = True

    def post(self, request):
        s = LogoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            token = RefreshToken(s.validated_data["refresh"])
            if str(token.get("user_id")) != str(request.user.pk):
                raise ValidationError({"refresh": "Token does not belong to this account."})
            token.blacklist()
        except TokenError:
            pass  # Already invalid: the client is logged out either way.
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        request.user.set_password(s.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        services.blacklist_all_tokens(request.user)
        return Response({"tokens": services.issue_tokens(request.user)})


class PasswordResetRequestView(APIView):
    serializer_class = PasswordResetRequestSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        s = PasswordResetRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.send_password_reset(s.validated_data["email"])
        return Response({"detail": "If that email is registered, we sent a 6-digit code to it."})


class PasswordResetConfirmView(APIView):
    serializer_class = PasswordResetConfirmSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        s = PasswordResetConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.reset_password(**s.validated_data)
        return Response({"detail": "Password updated. You can now log in."})


class PasswordResetCodeVerifyView(APIView):
    """
    POST /api/auth/password/reset/code/verify/ {email, code}
    Checks the emailed code. Returns {reset_token} (10 minutes, single use)
    for setting the new password. A wrong code answers 400 with error code
    details on the "code" field; each wrong try counts toward the limit.
    """

    serializer_class = PasswordResetCodeVerifySerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        s = PasswordResetCodeVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response({"reset_token": services.verify_reset_code(**s.validated_data)})


class PasswordResetCodeView(APIView):
    """
    POST /api/auth/password/reset/code/ {reset_token, new_password}
    Sets the new password after the code was verified. Older clients may
    send {email, code, new_password} instead.
    """

    serializer_class = PasswordResetCodeSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        s = PasswordResetCodeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("reset_token"):
            services.reset_password_with_token(d["reset_token"], d["new_password"])
        else:
            services.reset_password_with_code(d["email"], d["code"], d["new_password"])
        return Response({"detail": "Password updated. You can now log in."})


class UsernameAvailabilityView(APIView):
    serializer_class = UserListSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LookupThrottle]

    def get(self, request):
        username = (request.query_params.get("username") or "").strip()
        reason = None
        try:
            validate_username(username)
        except Exception as exc:  # django ValidationError
            reason = exc.messages[0] if hasattr(exc, "messages") else "Invalid username."
        if reason is None and User.objects.filter(username__iexact=username).exists():
            reason = "This username is taken."
        return Response({"username": username, "available": reason is None, "reason": reason})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class EmailVerifyView(APIView):
    """
    POST /api/auth/email/verify/ {code} — confirm the email with the code sent
    after sign-up. Returns the account (email_verified: true).
    """

    serializer_class = EmailCodeSerializer
    allow_unverified = True

    def post(self, request):
        s = EmailCodeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.verify_email_code(request.user, s.validated_data["code"])
        user = selectors.with_follow_flags(selectors.base_users(), request.user).get(pk=request.user.pk)
        return Response(MeSerializer(user, context={"request": request}).data)


class EmailResendView(APIView):
    """POST /api/auth/email/resend/ — a new code (45 s apart, 5 an hour)."""

    serializer_class = EmailChangeSerializer
    allow_unverified = True

    def post(self, request):
        if request.user.email_verified:
            return Response({"detail": "Your email is already confirmed.", "email": request.user.email})
        services.send_email_code(request.user, enforce_cooldown=True)
        return Response({"detail": "We sent a new code.", "email": request.user.email})


class EmailChangeView(APIView):
    """POST /api/auth/email/change/ {email} — fix a mistyped address before confirming it."""

    serializer_class = EmailChangeSerializer
    allow_unverified = True

    def post(self, request):
        s = EmailChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.change_unconfirmed_email(request.user, s.validated_data["email"])
        user = selectors.with_follow_flags(selectors.base_users(), request.user).get(pk=request.user.pk)
        return Response(MeSerializer(user, context={"request": request}).data)


class MeView(APIView):
    serializer_class = MeSerializer
    # Before the email is confirmed: read your account or delete it, nothing else.
    allow_unverified_methods = ("GET", "DELETE")

    def _me(self, request):
        return selectors.with_follow_flags(selectors.base_users(), request.user).get(pk=request.user.pk)

    def get(self, request):
        return Response(MeSerializer(self._me(request), context={"request": request}).data)

    def patch(self, request):
        was_private = request.user.is_private
        s = UpdateMeSerializer(request.user, data=request.data, partial=True, context={"request": request})
        s.is_valid(raise_exception=True)
        s.save()
        if was_private and not request.user.is_private:
            # Going public lets everyone who asked in.
            follow_services.approve_all_requests(request.user)
        return Response(MeSerializer(self._me(request), context={"request": request}).data)

    def delete(self, request):
        s = DeleteAccountSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not request.user.check_password(s.validated_data["password"]):
            raise ValidationError({"password": "Password is incorrect."})
        with transaction.atomic():
            from apps.follows.services import unfollow

            for f in Follow.objects.filter(follower=request.user).select_related("following"):
                unfollow(request.user, f.following)
            for f in Follow.objects.filter(following=request.user).select_related("follower"):
                unfollow(f.follower, request.user)
            request.user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDetailView(generics.RetrieveAPIView):
    serializer_class = UserProfileSerializer

    def get_queryset(self):
        # Someone who blocked you is not found; someone you blocked stays visible so you can unblock.
        viewer = self.request.user
        qs = selectors.base_users().filter(Q(email_verified=True) | Q(pk=viewer.pk)).exclude(pk__in=blocking_me(viewer))
        qs = qs.annotate(
            is_blocked=Exists(Block.objects.filter(blocker=viewer, blocked=OuterRef("pk"))),
            is_requested=Exists(FollowRequest.objects.filter(requester=viewer, target=OuterRef("pk"))),
        )
        return selectors.with_follow_flags(qs, viewer)


class UserByUsernameView(UserDetailView):
    def get_object(self):
        return get_object_or_404(self.get_queryset(), username__iexact=self.kwargs["username"])


class FollowView(APIView):
    serializer_class = UserListSerializer

    def _target(self, pk):
        return get_object_or_404(User.objects.filter(is_active=True), pk=pk)

    def post(self, request, pk):
        """Follow, or send a request to a private account (follow_status "requested")."""
        target = self._target(pk)
        state, created = follow_services.follow_or_request(request.user, target)
        target.refresh_from_db(fields=["followers_count"])
        return Response(
            {
                "is_following": state == "following",
                "follow_status": state,
                "followers_count": target.followers_count,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        """Unfollow, or cancel a pending request."""
        target = self._target(pk)
        follow_services.cancel_request(request.user, target)
        follow_services.unfollow(request.user, target)
        target.refresh_from_db(fields=["followers_count"])
        return Response({"is_following": False, "follow_status": "none", "followers_count": target.followers_count})


class FollowRequestsView(APIView):
    """GET /api/users/me/follow-requests/ — people waiting for approval (private accounts)."""

    serializer_class = UserListSerializer

    def get(self, request):
        ids = FollowRequest.objects.filter(target=request.user).values("requester_id")
        qs = selectors.with_follow_flags(selectors.base_users(request.user).filter(pk__in=ids), request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs.order_by("-pk"), request, view=self)
        return paginator.get_paginated_response(UserListSerializer(page, many=True, context={"request": request}).data)


class FollowRequestActionView(APIView):
    """POST /api/users/me/follow-requests/{user_id}/approve/ or .../decline/"""

    serializer_class = UserListSerializer

    def post(self, request, pk, action):
        if action == "approve":
            follow_services.approve_request(request.user, pk)
        elif action == "decline":
            follow_services.decline_request(request.user, pk)
        else:
            raise NotFound()
        return Response({"ok": True})


class PresenceView(APIView):
    """
    POST /api/users/me/presence/ {"state": "online" | "offline"}
    The app reports when it comes to the foreground (and every minute while open)
    and when it goes to the background. Last seen is kept either way.
    """

    serializer_class = PresenceSerializer

    def post(self, request):
        s = PresenceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        online = s.validated_data["state"] == "online"
        User.objects.filter(pk=request.user.pk).update(presence_online=online, last_seen_at=timezone.now())
        return Response({"state": s.validated_data["state"]})


class RemoveFollowerView(APIView):
    """Remove someone who follows you."""

    serializer_class = UserListSerializer

    def delete(self, request, pk):
        follower = get_object_or_404(User, pk=pk)
        follow_services.unfollow(follower, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _FollowListBase(generics.ListAPIView):
    serializer_class = UserListSerializer
    relation = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return User.objects.none()
        target = get_object_or_404(selectors.base_users(self.request.user), pk=self.kwargs["pk"])
        viewer = self.request.user
        follows_target = Follow.objects.filter(follower=viewer, following=target).exists()
        if target.is_private and target.pk != viewer.pk and not follows_target:
            return User.objects.none()
        if self.relation == "followers":
            ids = Follow.objects.filter(following=target).values("follower_id")
        else:
            ids = Follow.objects.filter(follower=target).values("following_id")
        qs = selectors.base_users(self.request.user).filter(pk__in=ids)
        q = self.request.query_params.get("q")
        if q:
            from django.db.models import Q

            qs = qs.filter(Q(username__icontains=q) | Q(full_name__icontains=q))
        return selectors.with_follow_flags(qs, self.request.user).order_by("username")


class FollowersView(_FollowListBase):
    relation = "followers"


class FollowingView(_FollowListBase):
    relation = "following"


class SuggestedUsersView(APIView):
    """
    GET /api/users/suggested/ — People you may know, paginated for scrolling
    (``page``, ``page_size`` up to 50; ``limit`` is accepted as the page size).
    """

    serializer_class = SuggestedUserSerializer

    def get(self, request):
        paginator = StandardPagination()
        if "limit" in request.query_params and "page_size" not in request.query_params:
            try:
                paginator.page_size = max(1, min(int(request.query_params["limit"]), paginator.max_page_size))
            except ValueError:
                pass
        page = paginator.paginate_queryset(selectors.suggested_users_queryset(request.user), request, view=self)
        context = {"request": request, "suggestions": selectors.suggestion_context(request.user, page)}
        return paginator.get_paginated_response(SuggestedUserSerializer(page, many=True, context=context).data)
