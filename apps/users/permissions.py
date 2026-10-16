from rest_framework.permissions import IsAuthenticated


class IsVerifiedUser(IsAuthenticated):
    """
    Signed in with a confirmed email. Accounts that have not entered their
    email code yet can only reach views that opt in with
    ``allow_unverified = True`` or list the method in
    ``allow_unverified_methods`` (the verification screen's own calls,
    reading their profile, signing out, deleting the account).
    """

    message = "Confirm your email to continue."
    code = "email_unverified"

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if getattr(request.user, "email_verified", True):
            return True
        if getattr(view, "allow_unverified", False):
            return True
        return request.method in getattr(view, "allow_unverified_methods", ())
