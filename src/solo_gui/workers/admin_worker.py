"""Admin worker for SoloKeys GUI using DeviceManager.

Solo2 admin app commands for device management:
- Reboot to bootloader/regular mode
- Factory reset
- Device diagnostics

Uses DeviceManager for thread-safe device access.
"""

import logging
import os

from PySide6.QtCore import QObject, Signal

_log = logging.getLogger("solo_gui.admin_worker")

from solo2.admin import AdminCommand, AdminSession, DeviceDiagnostics, RebootMode
from solo2.bootloader import BootloaderError, BootloaderSession
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
    unlock_ready = Signal()
    relock_ready = Signal()

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
        """Detect Hacker/Secure variant via MCUBOOT ISP (CMPA read)."""
        self.operation_started.emit("Checking device variant via hardware ISP…")

        def _progress(pct: int, msg: str) -> None:
            self.operation_progress.emit(pct, msg)

        try:
            result = lpc55_isp.check_variant_with_device(self._device, progress_cb=_progress)
        except lpc55_isp.Lpc55Error as exc:
            self.error_occurred.emit(f"ISP probe failed: {exc}")
            return

        self.operation_completed.emit(True, f"Variant: {result}")
        self.variant_ready.emit(result)

    def _wait_for_bootloader(self, timeout_s: float) -> bool:
        """Wait for the ROM bootloader using solo2's full HID fallback path."""
        try:
            session = BootloaderSession.find(timeout=timeout_s)
        except BootloaderError as exc:
            _log.debug("BootloaderSession fallback did not find bootloader: %s", exc)
            return lpc55_isp.wait_for_bootloader(timeout_s=0.5)

        session.close()
        return True

    def unlock_device(self, pfr_yaml_path: str = "") -> None:
        """
        Disable Secure Boot on a Hacker (locked) device via MCUBOOT ISP.

        Reboots to bootloader, reads the signed firmware backup, zeroes the
        CMPA SHA256 digest field (which disables Secure Boot on LPC55), then
        reboots back to firmware.

        If pfr_yaml_path is provided:
          - The PFR YAML backup is saved to pfr_yaml_path.
          - The signed firmware backup is saved to the same path with a .bin
            extension (e.g. pfr_backup.yaml → pfr_backup.bin).
        Both files are required for relock_device() to restore factory state.
        """
        self.operation_started.emit("Disabling Secure Boot…")

        try:
            AdminSession(self._device).reboot(RebootMode.BOOTLOADER)
        except Exception as exc:
            _log.debug("unlock_device: reboot-to-bootloader raised (expected): %s", exc)

        self.operation_progress.emit(
            10,
            "Waiting for bootloader — press the Solo 2 button now if it is blinking…",
        )
        if not self._wait_for_bootloader(timeout_s=20.0):
            self.error_occurred.emit(
                "Bootloader device did not appear within 20 s.\n"
                "Make sure the device is connected, press the Solo 2 button when "
                "it asks for touch confirmation, "
                "and try again."
            )
            return

        try:
            pfr_yaml, firmware = lpc55_isp.disable_secure_boot(
                progress_cb=lambda pct, msg: self.operation_progress.emit(
                    10 + int(pct * 85 / 100), msg
                )
            )
        except lpc55_isp.Lpc55Error as exc:
            self.error_occurred.emit(f"Unlock failed: {exc}")
            return

        if pfr_yaml_path:
            try:
                with open(pfr_yaml_path, "w") as f:
                    f.write(pfr_yaml)
            except OSError as exc:
                _log.warning("Failed to save PFR YAML backup to %s: %s", pfr_yaml_path, exc)

            if firmware:
                fw_path = os.path.splitext(pfr_yaml_path)[0] + ".bin"
                try:
                    with open(fw_path, "wb") as f:
                        f.write(firmware)
                    _log.debug("unlock_device: firmware backup saved to %s (%d B)", fw_path, len(firmware))
                except OSError as exc:
                    _log.warning("Failed to save firmware backup to %s: %s", fw_path, exc)
            else:
                _log.warning("unlock_device: no firmware backup available (flash was blank)")

        self.operation_progress.emit(100, "Done")
        self.operation_completed.emit(True, "Secure Boot disabled — device is now unlocked")
        self.unlock_ready.emit()

    def relock_device(self, pfr_yaml_path: str) -> None:
        """Re-enable Secure Boot using a saved PFR YAML backup.

        Loads the PFR YAML backup from pfr_yaml_path and the signed firmware
        backup from the same path with a .bin extension (e.g. pfr_backup.yaml
        → pfr_backup.bin).  Both are saved by unlock_device().

        The firmware backup is used to restore the original signed firmware
        after erase_all.  If no .bin backup is found, the firmware currently
        in flash is used as fallback (only works if it is already the original
        SoloKeys-signed build).
        """
        self.operation_started.emit("Relocking device…")

        try:
            with open(pfr_yaml_path, "r") as f:
                pfr_yaml = f.read()
        except OSError as exc:
            self.error_occurred.emit(f"Cannot read PFR YAML backup: {exc}")
            return

        # Load firmware backup saved alongside the YAML during unlock.
        firmware = b""
        fw_path = os.path.splitext(pfr_yaml_path)[0] + ".bin"
        if os.path.exists(fw_path):
            try:
                with open(fw_path, "rb") as f:
                    firmware = f.read()
                _log.debug("relock_device: loaded firmware backup from %s (%d B)", fw_path, len(firmware))
            except OSError as exc:
                _log.warning("Failed to load firmware backup from %s: %s", fw_path, exc)
        else:
            _log.debug("relock_device: no firmware backup at %s — will read from flash", fw_path)

        def _progress(pct: int, msg: str) -> None:
            self.operation_progress.emit(pct, msg)

        try:
            lpc55_isp.relock_with_device(
                self._device, pfr_yaml, firmware, progress_cb=_progress
            )
        except lpc55_isp.Lpc55Error as exc:
            self.error_occurred.emit(f"Relock failed: {exc}")
            return

        self.operation_completed.emit(True, "Device relocked — Secure Boot re-enabled")
        self.relock_ready.emit()

    def wink(self) -> None:
        """Wink device LED."""
        def on_wink(result, error):
            if error:
                self.error_occurred.emit(f"Wink failed: {error}")
            else:
                self.operation_completed.emit(True, "Device winked")

        self._device_manager.wink(on_wink, operation_id="admin_wink")
