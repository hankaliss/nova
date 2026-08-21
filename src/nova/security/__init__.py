"""Security for Nova."""

from nova.security.permissions import PermissionDenied, PermissionSystem

__all__ = ["PermissionDenied", "PermissionSystem"]