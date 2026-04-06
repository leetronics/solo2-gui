"""Admin worker for SoloKeys GUI using DeviceManager.

Solo2 admin app commands for device management:
- Reboot to bootloader/regular mode
- Factory reset
- Device diagnostics

Uses DeviceManager for thread-safe device access.
"""

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

_log = logging.getLogger("solo_gui.admin_worker")

from solo2.admin import AdminCommand, AdminSession, DeviceDiagnostics, RebootMode
from solo2 import lpc55_isp
from solo_gui.device_manager import DeviceManager


class AdminWorker(QObject):
    """Worker for admin operations on Solo2 devices."""

    operation_started = Signal(str)
    operation_progress = Signal(int, str)
    operation_completed = Signal(bool, str)
    error_occurred = Signal(str)
    diagnostics_ready = Signal(object)
    uuid_ready = Signal(str)
    reboot_requested = Signal(int)
    device_disconnected = Signal()
    variant_ready = Signal(str)

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._device_manager = DeviceManager.get_instance()
    
    def _on_dm_operation_completed(self, op_id, success, message):
        """Forward DeviceManager completion signals."""
        if op_id.startswith("admin_"):
            self.operation_completed.emit(success, message)
    
    def _on_dm_error(self, op_id, error):
        """Forward DeviceManager error signals."""
        if op_id.startswith("admin_"):
            self.error_occurred.emit(error)

    def _require_capability(self, flag: bool, name: str) -> bool:
        """Emit error and return False if capability is not supported."""
        if not flag:
            self.error_occurred.emit(f"'{name}' not supported by this firmware")
            return False
        return True

    def get_uuid(self) -> None:
        """Get device UUID via admin command 0x62."""
        caps = self._device.capabilities if self._device else None
        if not self._require_capability(caps is not None and caps.has_uuid, "UUID"):
            return

        def on_response(response, error):
            if error:
                self.error_occurred.emit(f"Failed to get UUID: {error}")
            elif response and len(response) >= 16:
                uuid_hex = response[:16].hex()
                uuid_str = f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:32]}"
                self.uuid_ready.emit(uuid_str)
                self.operation_completed.emit(True, f"UUID: {uuid_str}")
            else:
                self.error_occurred.emit("No valid UUID response from device")

        self._device_manager.vendor_command(AdminCommand.UUID, b'', on_response, operation_id="admin_uuid")

    def get_diagnostics(self) -> None:
        """Get device diagnostics."""
        self.operation_started.emit("Getting device diagnostics")
        diagnostics = DeviceDiagnostics()
        caps = self._device.capabilities if self._device else None

        if caps:
            diagnostics.firmware_version = caps.firmware_version or ""
            diagnostics.ctap2_options = {
                'clientPin': caps.ctap2_pin,
                'credMgmt': caps.ctap2_cred_mgmt,
                'uv': caps.ctap2_uv,
                'rk': caps.ctap2_rk,
                'up': caps.ctap2_up,
            }

        if caps and caps.has_uuid:
            def on_uuid(response, error):
                if response and len(response) >= 16:
                    uuid_hex = response[:16].hex()
                    diagnostics.uuid = f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:32]}"
                self._get_lock_status(diagnostics, caps)
            
            self._device_manager.vendor_command(AdminCommand.UUID, b'', on_uuid, operation_id="admin_diag_uuid")
        else:
            self._get_lock_status(diagnostics, caps)

    def _get_lock_status(self, diagnostics, caps):
        """Get lock status as part of diagnostics."""
        if caps and caps.has_locked:
            def on_locked(response, error):
                if response and len(response) >= 1:
                    diagnostics.is_locked = response[0] != 0
                self.diagnostics_ready.emit(diagnostics)
                self.operation_completed.emit(True, "Diagnostics collected successfully")
            
            self._device_manager.vendor_command(AdminCommand.LOCKED, b'', on_locked, operation_id="admin_diag_locked")
        else:
            self.diagnostics_ready.emit(diagnostics)
            self.operation_completed.emit(True, "Diagnostics collected successfully")

    def reboot(self, mode: RebootMode = RebootMode.REGULAR) -> None:
        """Reboot device to specified mode."""
        caps = self._device.capabilities if self._device else None
        mode_names = {RebootMode.REGULAR: "regular mode", RebootMode.BOOTLOADER: "bootloader mode"}

        if mode == RebootMode.BOOTLOADER:
            if not self._require_capability(caps is not None and caps.has_boot_to_bootloader, "Boot to bootloader"):
                return
            cmd = AdminCommand.BOOT_TO_BOOTLOADER
        else:
            if not self._require_capability(caps is not None and caps.has_reboot, "Reboot"):
                return
            cmd = AdminCommand.REBOOT

        self.operation_started.emit(f"Rebooting to {mode_names.get(mode, 'unknown')}")
        self.reboot_requested.emit(mode)

        def on_response(response, error):
            self.device_disconnected.emit()
            self.operation_completed.emit(True, f"Rebooting to {mode_names.get(mode, 'unknown')}")

        self._device_manager.vendor_command(cmd, b'', on_response, operation_id="admin_reboot")

    def factory_reset(self, confirm: bool = False) -> None:
        """Perform factory reset using DeviceManager."""
        if not confirm:
            self.error_occurred.emit("Factory reset requires confirmation")
            return

        self.operation_started.emit("Factory reset")

        def on_reset(result, error):
            if error or not result:
                if error is None:
                    self.error_occurred.emit("Factory reset did not complete")
                    return
                err = str(error)
                if "0x30" in err or "NOT_ALLOWED" in err:
                    self.error_occurred.emit("Reset window expired (must be within 10 s of boot). Please try again.")
                elif "OperationDenied" in err or "0x27" in err:
                    self.error_occurred.emit("Factory reset cancelled (no touch confirmed).")
                elif "UserActionTimeout" in err or "timeout" in err.lower():
                    self.error_occurred.emit("No touch detected in time. Please try again and touch the device when prompted.")
                else:
                    self.error_occurred.emit(f"Factory reset failed: {error}")
            else:
                self.operation_completed.emit(True, "FIDO2 and Secrets/Vault data have been reset")

        self._device_manager.reset(on_reset, operation_id="admin_reset")

    def check_variant(self) -> None:
        """
        Detect Hacker/Secure variant via MCUBOOT ISP.

        If the device is already known to be unlocked (variant == "Hacker"), returns
        "Hacker (unlocked)" immediately without rebooting.

        Otherwise reboots to bootloader, probes CMPA via MCUBOOT ISP:
          "Secure"          — ISP read blocked (genuine Secure device)
          "Hacker (locked)" — ISP read allowed (Hacker device, Secure Boot still active)
        """
        # Fast path: firmware already confirmed the device is unlocked
        if getattr(self._device, "variant", "") == "Hacker":
            self.operation_completed.emit(True, "Variant: Hacker (unlocked)")
            self.variant_ready.emit("Hacker (unlocked)")
            return

        self.operation_started.emit("Checking device variant via hardware ISP…")

        # Reboot to bootloader (device will disconnect — exception is expected)
        try:
            AdminSession(self._device).reboot(RebootMode.BOOTLOADER)
        except Exception as exc:
            _log.debug("check_variant: reboot-to-bootloader raised (expected): %s", exc)

        # Wait for the bootloader USB device to appear
        self.operation_progress.emit(30, "Waiting for bootloader…")
        if not lpc55_isp.wait_for_bootloader(timeout_s=10.0):
            self.error_occurred.emit(
                "Bootloader device did not appear within 10 s.\n"
                "Make sure the device is connected and try again."
            )
            return

        # ISP variant probe
        self.operation_progress.emit(60, "Probing CMPA via ISP…")
        try:
            result = lpc55_isp.detect_variant()
        except lpc55_isp.Lpc55Error as exc:
            self.error_occurred.emit(f"ISP probe failed: {exc}")
            return

        self.operation_progress.emit(100, "Done")
        self.operation_completed.emit(True, f"Variant: {result}")
        self.variant_ready.emit(result)

    def wink(self) -> None:
        """Wink device LED."""
        def on_wink(result, error):
            if error:
                self.error_occurred.emit(f"Wink failed: {error}")
            else:
                self.operation_completed.emit(True, "Device winked")

        self._device_manager.wink(on_wink, operation_id="admin_wink")
