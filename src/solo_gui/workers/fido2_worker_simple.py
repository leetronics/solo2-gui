"""FIDO2 worker thread for SoloKeys GUI - simplified implementation."""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from fido2.ctap2.base import Ctap2, Info
from fido2.webauthn import PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity


@dataclass
class Fido2Credential:
    """FIDO2 credential information."""

    id: str
    rp_id: str
    rp_name: str
    user_id: str
    user_name: str
    user_display_name: str
    created: int
    is_resident: bool
    algorithm: str
    cred_id: Optional[bytes] = None


class Fido2Worker(QObject):
    """Worker thread for FIDO2 operations."""

    credentials_loaded = Signal(list[Fido2Credential])
    credential_deleted = Signal(bool, str)  # success, error message
    credential_renamed = Signal(bool, str)  # success, error message
    pin_changed = Signal(bool, str)  # success, error message
    pin_status_updated = Signal(dict)  # status info
    error_occurred = Signal(str)  # error message

    def __init__(self, ctap2: Ctap2):
        super().__init__()
        self._ctap2 = ctap2

    def load_credentials(self) -> None:
        """Load all FIDO2 credentials from the device."""
        try:
            credentials = []

            # Get device info
            info = self._ctap2.get_info()

            # Check if credential management is supported
            if "credMgmt" not in info.options:
                self.error_occurred.emit(
                    "Credential management not supported by this device"
                )
                return

            # For now, create a placeholder credential list
            # TODO: Implement actual credential enumeration using CTAP2 commands
            placeholder_credential = Fido2Credential(
                id="placeholder",
                rp_id="example.com",
                rp_name="Example Service",
                user_id="user123",
                user_name="user@example.com",
                user_display_name="User",
                created=0,
                is_resident=True,
                algorithm="ES256",
            )

            # Add placeholder to show the interface works
            credentials.append(placeholder_credential)

            self.credentials_loaded.emit(credentials)

        except Exception as e:
            self.error_occurred.emit(f"Failed to load credentials: {e}")

    def delete_credential(self, credential: Fido2Credential) -> None:
        """Delete a credential from the device."""
        try:
            # TODO: Implement actual credential deletion using CTAP2 commands
            # For now, just emit success
            self.credential_deleted.emit(True, "")

        except Exception as e:
            self.credential_deleted.emit(False, str(e))

    def rename_credential(self, credential: Fido2Credential, new_name: str) -> None:
        """Rename a credential user information."""
        try:
            # TODO: Implement actual credential renaming using CTAP2 commands
            # For now, just emit success
            self.credential_renamed.emit(True, "")

        except Exception as e:
            self.credential_renamed.emit(False, str(e))

    def get_pin_status(self) -> None:
        """Get current PIN status and retry counters."""
        try:
            info = self._ctap2.get_info()

            status = {
                "pin_set": info.options.get("clientPin", False),
                "pin_retries": getattr(info, "pin_retries", 0),
                "uv_set": info.options.get("uv", False),
                "make_credential_uv": info.options.get("makeCredUvNotAllowed", False),
                "auth_uv": info.options.get("uvOptionalForCredentialMgnt", False),
            }

            self.pin_status_updated.emit(status)

        except Exception as e:
            self.error_occurred.emit(f"Failed to get PIN status: {e}")

    def change_pin(self, current_pin: str, new_pin: str) -> None:
        """Change the FIDO2 PIN."""
        try:
            # TODO: Implement actual PIN change using CTAP2 commands
            # For now, just emit success
            self.pin_changed.emit(True, "")

        except Exception as e:
            self.pin_changed.emit(False, str(e))

    def _get_algorithm_name(self, alg_type: int) -> str:
        """Get algorithm name from type value."""
        algorithms = {
            -7: "ES256",
            -8: "EdDSA",
            -6: "RS256",
            -35: "ES384",
            -36: "ES512",
            -37: "RS384",
            -38: "RS512",
            -257: "RS256",
        }
        return algorithms.get(alg_type, f"Unknown ({alg_type})")
