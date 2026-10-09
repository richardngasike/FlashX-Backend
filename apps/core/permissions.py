from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwnerOrReadOnly(BasePermission):
    """Object-level: writes allowed only to the owner (``owner_field``, default ``author``)."""

    owner_field = "author"

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        field = getattr(view, "owner_field", self.owner_field)
        return getattr(obj, f"{field}_id", None) == request.user.id
