"""TOTP/Secrets worker for SoloKeys GUI using the standalone solo2 package."""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal

from solo2.secrets import (
    Algorithm,
    Credential,
    OtpKind,
    OtpResult,
    OtherKind,
    SecretsAppStatus,
    SecretsSession,
    encode_password_only_label,
    is_password_only_label,
    strip_password_only_label,
)
from solo2.errors import Solo2PinRequiredError, Solo2TouchRequiredError
from solo_gui.device_manager import DeviceManager

PASSWORD_ONLY_PREFIX = "__solo_pw__:"


class TotpWorker(QObject):
    """Qt adapter around the standalone solo2 secrets session."""

    status_checked = Signal(object)
    credentials_loaded = Signal(list)
    credential_added = Signal(bool, str)
    credential_updated = Signal(bool, str)
    credential_deleted = Signal(bool, str)
    credential_data_loaded = Signal(object)
    otp_generated = Signal(object)
    reverse_hotp_verified = Signal(bool, str)
    hmac_calculated = Signal(str)
    pin_verified = Signal(bool, str)
    pin_changed = Signal(bool, str)
    pin_required = Signal()
    touch_required = Signal()
    error_occurred = Signal(str)

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._device_manager = DeviceManager.get_instance()
        self._pin: Optional[str] = None
        self._pin_verified: bool = False
        self._pin_is_set: bool = False
        self._session = SecretsSession(transport=self._send_apdu_sync)

    @property
    def pin_is_set(self) -> bool:
        return self._pin_is_set

    @property
    def pin_is_verified(self) -> bool:
        return self._pin_verified

    def set_pin(self, pin: str) -> None:
        self._pin = pin
        self._pin_verified = False

    def clear_pin(self) -> None:
        self._pin = None
        self._pin_verified = False

    def _send_apdu_sync(self, apdu_bytes: bytes) -> bytes:
        result_holder: list[bytes | None] = [None]
        error_holder: list[str | None] = [None]
        done = threading.Event()

        def callback(result, error):
            result_holder[0] = bytes(result) if result is not None else None
            error_holder[0] = error
            done.set()

        self._device_manager.send_browser_apdu(apdu_bytes, callback)
        if not done.wait(timeout=10):
            raise RuntimeError("Timeout waiting for device")
        if error_holder[0]:
            raise RuntimeError(str(error_holder[0]))
        if result_holder[0] is None:
            raise RuntimeError("No response from device")
        return result_holder[0]

    def check_status(self) -> None:
        try:
            status = self._session.get_status()
            self._pin_is_set = bool(status.pin_set)
            self.status_checked.emit(status)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def load_credentials(self) -> None:
        try:
            credentials = self._session.list_credentials()
            self.credentials_loaded.emit(credentials)
        except Solo2PinRequiredError:
            self.pin_required.emit()
        except Exception as exc:
            self.error_occurred.emit(f"Failed to list credentials: {exc}")

    def add_credential(self, credential: Credential, secret: bytes) -> None:
        try:
            self._session.add_credential(credential, secret)
            self.credential_added.emit(True, "")
        except Solo2PinRequiredError:
            self.pin_required.emit()
            self.credential_added.emit(False, "PIN required")
        except Solo2TouchRequiredError:
            self.touch_required.emit()
            self.credential_added.emit(False, "Touch required")
        except Exception as exc:
            self.credential_added.emit(False, str(exc))

    def delete_credential(self, credential: Credential) -> None:
        try:
            self._session.delete_credential(credential)
            self.credential_deleted.emit(True, "")
        except Exception as exc:
            self.credential_deleted.emit(False, f"Failed to delete: {exc}")

    def generate_otp(self, credential: Credential, touch_confirmed: bool = False) -> None:
        del touch_confirmed
        try:
            result = self._session.generate_otp(credential)
            self.otp_generated.emit(result)
        except Solo2PinRequiredError:
            self.pin_required.emit()
        except Solo2TouchRequiredError:
            self.touch_required.emit()
        except Exception as exc:
            self.error_occurred.emit(f"Failed to generate OTP: {exc}")

    def load_credential_data(self, credential: Credential) -> None:
        try:
            data = self._session.get_credential(credential)
            self.credential_data_loaded.emit(data)
        except Solo2PinRequiredError:
            self.pin_required.emit()
        except Solo2TouchRequiredError:
            self.touch_required.emit()
        except Exception as exc:
            self.error_occurred.emit(f"Failed to get credential data: {exc}")

    def update_credential_data(
        self,
        credential: Credential,
        *,
        new_name: Optional[str] = None,
        login: Optional[str] = None,
        password: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> None:
        try:
            self._session.update_credential(
                credential,
                new_name=new_name,
                login=login,
                password=password,
                metadata=metadata,
            )
            self.credential_updated.emit(True, "")
        except Solo2PinRequiredError:
            self.pin_required.emit()
        except Solo2TouchRequiredError:
            self.touch_required.emit()
        except Exception as exc:
            self.credential_updated.emit(False, f"Failed to update credential: {exc}")

    def verify_reverse_hotp(self, credential: Credential, code: str) -> None:
        try:
            self._session.verify_reverse_hotp(credential, code)
            self.reverse_hotp_verified.emit(True, "Verification passed")
        except Solo2PinRequiredError:
            self.pin_required.emit()
        except Solo2TouchRequiredError:
            self.touch_required.emit()
        except Exception as exc:
            self.reverse_hotp_verified.emit(False, f"Verification failed: {exc}")

    def calculate_hmac(self, slot: int, challenge: bytes) -> None:
        try:
            result = self._session.calculate_hmac(slot, challenge)
            self.hmac_calculated.emit(result)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def verify_pin(self, pin: str) -> None:
        result = self._session.verify_pin(pin)
        if result.get("success"):
            self._pin = pin
            self._pin_verified = True
            self._pin_is_set = True
            self.pin_verified.emit(True, "")
            return
        self.pin_verified.emit(False, result.get("error", "PIN verification failed"))

    def set_new_pin(self, pin: str) -> None:
        result = self._session.set_pin(pin)
        if result.get("success"):
            self._pin = pin
            self._pin_verified = True
            self._pin_is_set = True
            self.pin_changed.emit(True, "")
            return
        self.pin_changed.emit(False, result.get("error", "Failed to set PIN"))

    def change_pin(self, old_pin: str, new_pin: str) -> None:
        result = self._session.change_pin(old_pin, new_pin)
        if result.get("success"):
            self._pin = new_pin
            self._pin_verified = True
            self.pin_changed.emit(True, "")
            return
        self.pin_changed.emit(False, result.get("error", "Failed to change PIN"))


class FirmwareExtensionSpec:
    """Compatibility documentation hook used by the GUI."""

    @staticmethod
    def get_integration_plan() -> str:
        return """
Firmware Extension: TOTP/Secrets Application
==============================================

OATH-compatible secrets app for Solo2 firmware.
Uses CTAPHID vendor command 0x70 with ISO 7816 APDU format.

Supported Commands:
- LIST (0xa1): List all credentials
- PUT (0x01): Add/register credential
- DELETE (0x02): Delete credential
- CALCULATE (0xa2): Generate OTP code
- VALIDATE (0xa3): Verify PIN
- SET_PIN (0xb4): Set initial PIN
- CHANGE_PIN (0xb3): Change PIN
"""


__all__ = [
    "Algorithm",
    "Credential",
    "OtpKind",
    "OtpResult",
    "OtherKind",
    "PASSWORD_ONLY_PREFIX",
    "SecretsAppStatus",
    "FirmwareExtensionSpec",
    "TotpWorker",
    "encode_password_only_label",
    "is_password_only_label",
    "strip_password_only_label",
]
