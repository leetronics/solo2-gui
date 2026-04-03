"""Compatibility re-exports for the standalone solo2 secrets bridge."""

from solo2.secrets import (
    OATHBridge,
    OATHError,
    OATHPINRequired,
    OATHTouchRequired,
    PASSWORD_ONLY_PREFIX,
    encode_password_only_label,
    is_password_only_label,
    strip_password_only_label,
)

__all__ = [
    "OATHBridge",
    "OATHError",
    "OATHPINRequired",
    "OATHTouchRequired",
    "PASSWORD_ONLY_PREFIX",
    "encode_password_only_label",
    "is_password_only_label",
    "strip_password_only_label",
]
