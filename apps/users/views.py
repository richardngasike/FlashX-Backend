from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.exceptions import ServiceError
from apps.core.throttles import AuthRateThrottle, PasswordResetThrottle
from apps.follows import services as follow_services
from apps.follows.models import Follow

from . import selectors, services
from .serializers import (
    ChangePasswordSerializer,
    DeleteAccountSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
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
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
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
        user = services.authenticate_identifier(s.validated_data["identifier"], s.validated_data["password"])
        if user is None:
            # Explicit 401: the view has no authenticators, so AuthenticationFailed would become 403.
            raise ServiceError("Invalid username/email or password.", code="invalid_credentials", status_code=401)
        from django.contrib.auth.models import update_last_login

        update_last_login(None, user)
        user = selectors.with_follow_flags(selectors.base_users(), user).get(pk=user.pk)
        return Response(
            {"user": MeSerializer(user, context={"request": request}).data, "tokens": services.issue_tokens(user)}
        )


class RefreshView(TokenRefreshView):
    throttle_classes = [AuthRateThrottle]


class LogoutView(APIView):
    serializer_class = LogoutSerializer

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
        return Response({"detail": "If that email is registered, a reset link has been sent."})


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


class UsernameAvailabilityView(APIView):
    serializer_class = UserListSerializer

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthRateThrottle]

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
class MeView(APIView):
    serializer_class = MeSerializer

    def _me(self, request):
        return selectors.with_follow_flags(selectors.base_users(), request.user).get(pk=request.user.pk)

    def get(self, request):
        return Response(MeSerializer(self._me(request), context={"request": request}).data)

    def patch(self, request):
        s = UpdateMeSerializer(request.user, data=request.data, partial=True, context={"request": request})
        s.is_valid(raise_exception=True)
        s.save()
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
        return selectors.with_follow_flags(selectors.base_users(), self.request.user)


class UserByUsernameView(UserDetailView):
    def get_object(self):
        return get_object_or_404(self.get_queryset(), username__iexact=self.kwargs["username"])


class FollowView(APIView):
    serializer_class = UserListSerializer

    def _target(self, pk):
        return get_object_or_404(User.objects.filter(is_active=True), pk=pk)

    def post(self, request, pk):
        target = self._target(pk)
        created = follow_services.follow(request.user, target)
        target.refresh_from_db(fields=["followers_count"])
        return Response(
            {"is_following": True, "followers_count": target.followers_count},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        target = self._target(pk)
        follow_services.unfollow(request.user, target)
        target.refresh_from_db(fields=["followers_count"])
        return Response({"is_following": False, "followers_count": target.followers_count})


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
        target = get_object_or_404(User.objects.filter(is_active=True), pk=self.kwargs["pk"])
        if self.relation == "followers":
            ids = Follow.objects.filter(following=target).values("follower_id")
        else:
            ids = Follow.objects.filter(follower=target).values("following_id")
        qs = selectors.base_users().filter(pk__in=ids)
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
    serializer_class = UserListSerializer

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 50))
        except ValueError:
            limit = 20
        users = selectors.suggested_users(request.user, limit=limit)
        return Response({"results": UserListSerializer(users, many=True, context={"request": request}).data})
