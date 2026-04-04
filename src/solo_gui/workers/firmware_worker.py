"""Firmware update worker for SoloKeys GUI.

Solo2 firmware update process:
1. Check current firmware version via CTAP2 device info
2. Check for updates from GitHub releases
3. Download firmware binary
4. Reboot device to bootloader mode using admin app command
5. Flash firmware using bootloader protocol
6. Reboot back to regular mode
"""

from typing import Optional, Tuple, List
from dataclasses import dataclass
import os
import time
import hashlib

from PySide6.QtCore import QObject, Signal
import usb.core
import usb.util
import requests

from solo2.admin import AdminSession, RebootMode
from solo2.bootloader import BootloaderSession, BootloaderError


@dataclass
class FirmwareInfo:
    """Firmware version information."""

    version: str
    build_date: str
    size: int
    checksum: str
    release_notes: str
    download_url: str = ""


class FirmwareUpdateWorker(QObject):
    """Worker thread for firmware update operations."""

    update_started = Signal()
    update_progress = Signal(int, str)  # progress, message
    update_completed = Signal(bool, str)  # success, message
    error_occurred = Signal(str)  # error message
    firmware_info_found = Signal(object)  # FirmwareInfo or None
    bootloader_mode_changed = Signal(bool)  # True if in bootloader mode

    # Solo2 USB IDs
    SOLO2_VID = 0x1209
    SOLO2_PID_REGULAR = 0xBEEE  # Regular mode
    SOLO2_PID_BOOTLOADER = 0xB000  # Bootloader mode

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._bootloader = None

    def _open_hid_device(self):
        """Open HID device connection."""
        if not self._device:
            raise RuntimeError("No device available")
        hid_dev = self._device.open_hid_device()
        if hid_dev is None:
            raise RuntimeError("Device not found")
        return hid_dev

    def check_for_updates(self, current_version: str) -> None:
        """Check for available firmware updates from GitHub."""
        try:
            self.update_progress.emit(0, "Checking for updates...")

            # Get latest release info
            firmware_info = FirmwareRepo.get_latest_release_info()

            if not firmware_info:
                self.update_progress.emit(100, "Could not check for updates")
                self.firmware_info_found.emit(None)
                return

            # Compare versions
            if self._is_newer_version(firmware_info.version, current_version):
                self.update_progress.emit(
                    100,
                    f"Update available: {firmware_info.version} (current: {current_version})",
                )
                self.firmware_info_found.emit(firmware_info)
            else:
                self.update_progress.emit(
                    100, f"Firmware is up to date ({current_version})"
                )
                self.firmware_info_found.emit(None)

        except Exception as e:
            self.error_occurred.emit(f"Failed to check for updates: {e}")

    def _is_newer_version(self, new_version: str, current_version: str) -> bool:
        """Compare version strings."""
        try:
            new_parts = [int(x) for x in new_version.split(".")]
            current_parts = [int(x) for x in current_version.split(".")]

            # Pad to same length
            max_len = max(len(new_parts), len(current_parts))
            new_parts.extend([0] * (max_len - len(new_parts)))
            current_parts.extend([0] * (max_len - len(current_parts)))

            return new_parts > current_parts
        except Exception:
            return False

    def download_firmware(self, firmware_url: str) -> Optional[bytes]:
        """Download firmware file."""
        try:
            self.update_progress.emit(10, "Downloading firmware...")

            response = requests.get(firmware_url, stream=True, timeout=60)
            response.raise_for_status()

            firmware_data = b""
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    firmware_data += chunk
                    downloaded += len(chunk)

                    if total_size > 0:
                        progress = 10 + int((downloaded / total_size) * 30)
                        self.update_progress.emit(
                            progress,
                            f"Downloading: {downloaded // 1024}KB / {total_size // 1024}KB",
                        )

            self.update_progress.emit(40, "Download complete")
            return firmware_data

        except requests.exceptions.Timeout:
            self.error_occurred.emit("Download timed out")
            return None
        except requests.exceptions.RequestException as e:
            self.error_occurred.emit(f"Download failed: {e}")
            return None

    def verify_firmware(self, firmware_data: bytes, expected_hash: str = "") -> bool:
        """Verify firmware integrity."""
        try:
            self.update_progress.emit(45, "Verifying firmware integrity...")

            # Calculate SHA256 hash
            actual_hash = hashlib.sha256(firmware_data).hexdigest()

            if expected_hash:
                if actual_hash.lower() != expected_hash.lower():
                    self.error_occurred.emit(
                        f"Firmware hash mismatch!\n"
                        f"Expected: {expected_hash}\n"
                        f"Actual: {actual_hash}"
                    )
                    return False

            # Basic sanity checks on firmware
            if len(firmware_data) < 1024:
                self.error_occurred.emit("Firmware file too small")
                return False

            if len(firmware_data) > 512 * 1024:  # 512KB max
                self.error_occurred.emit("Firmware file too large")
                return False

            return True

        except Exception as e:
            self.error_occurred.emit(f"Verification failed: {e}")
            return False

    def reboot_to_bootloader(self) -> bool:
        """Reboot device to bootloader mode using admin app command."""
        try:
            self.update_progress.emit(50, "Rebooting to bootloader mode...")

            if not self._device:
                self.error_occurred.emit("Device not connected")
                return False

            try:
                AdminSession(self._device).reboot(RebootMode.BOOTLOADER)
            except Exception:
                pass

            # Wait for device to reboot
            self.update_progress.emit(55, "Waiting for bootloader...")
            time.sleep(1.0)

            # Check if bootloader is available
            with BootloaderSession.find(timeout=10):
                pass
            self.bootloader_mode_changed.emit(True)
            self.update_progress.emit(60, "Bootloader detected")
            return True

        except BootloaderError:
            self.error_occurred.emit(
                "Bootloader not detected. Please manually enter bootloader mode."
            )
            return False
        except Exception as e:
            self.error_occurred.emit(f"Failed to reboot to bootloader: {e}")
            return False

    def _find_bootloader(self) -> Optional[usb.core.Device]:
        """Find Solo2 in bootloader mode."""
        try:
            device = usb.core.find(
                idVendor=self.SOLO2_VID, idProduct=self.SOLO2_PID_BOOTLOADER
            )
            return device
        except Exception:
            return None

    def flash_firmware(self, firmware_data: bytes) -> bool:
        """Flash firmware to the device in bootloader mode."""
        try:
            self.update_progress.emit(65, "Preparing to flash...")
            total = len(firmware_data)

            def _progress(written: int, total_bytes: int) -> None:
                pct = 65 + int(written / total_bytes * 25)
                self.update_progress.emit(
                    pct, f"Writing: {written // 1024}/{total_bytes // 1024} KB"
                )

            with BootloaderSession.find(timeout=2) as bl:
                bl.write_flash(firmware_data, progress_cb=_progress)
            self.update_progress.emit(90, "Verifying flash...")
            return True

        except Exception as e:
            self.error_occurred.emit(f"Flash failed: {e}")
            return False

    def _build_write_command(self, address: int, data: bytes) -> bytes:
        """Build write command for bootloader."""
        # Solo2 bootloader command format (simplified):
        # [CMD_WRITE, addr_0, addr_1, addr_2, addr_3, len_0, len_1, data...]
        CMD_WRITE = 0x02
        cmd = bytes(
            [
                CMD_WRITE,
                (address >> 0) & 0xFF,
                (address >> 8) & 0xFF,
                (address >> 16) & 0xFF,
                (address >> 24) & 0xFF,
                len(data) & 0xFF,
                (len(data) >> 8) & 0xFF,
            ]
        )
        return cmd + data

    def reboot_to_regular(self) -> bool:
        """Reboot from bootloader back to regular mode."""
        try:
            self.update_progress.emit(95, "Rebooting device...")

            try:
                with BootloaderSession.find(timeout=2) as bl:
                    bl.reset()
            except BootloaderError:
                pass

            self.bootloader_mode_changed.emit(False)

            # Wait for device to reboot
            time.sleep(2)

            return True

        except Exception as e:
            self.error_occurred.emit(f"Reboot failed: {e}")
            return False

    def perform_update(self, firmware_info: FirmwareInfo) -> None:
        """Perform complete firmware update process."""
        self.update_started.emit()

        try:
            # Step 1: Download firmware
            firmware_data = self.download_firmware(firmware_info.download_url)
            if not firmware_data:
                return

            # Step 2: Verify firmware
            if not self.verify_firmware(firmware_data, firmware_info.checksum):
                return

            # Step 3: Reboot to bootloader, flash, and reboot back to firmware
            self._flash_firmware_bytes(firmware_data)
            self.update_progress.emit(100, "Update complete!")
            self.update_completed.emit(True, "Firmware updated successfully!")

        except Exception as e:
            self.error_occurred.emit(f"Update failed: {e}")
            # Try to recover
            try:
                self.reboot_to_regular()
            except Exception:
                pass

    # ------------------------------------------------------------------ flash_from_file

    def _flash_firmware_bytes(self, firmware_data: bytes) -> None:
        """Flash raw firmware bytes via the MCU bootloader HID protocol."""
        self.update_progress.emit(50, "Rebooting device to bootloader…")
        try:
            AdminSession(self._device).reboot(RebootMode.BOOTLOADER)
        except Exception:
            pass

        self.update_progress.emit(55, "Waiting for bootloader…")
        time.sleep(1.0)

        def _progress(written: int, total_bytes: int) -> None:
            pct = 65 + int(written / total_bytes * 25)
            self.update_progress.emit(
                pct, f"Writing: {written // 1024}/{total_bytes // 1024} KB"
            )

        with BootloaderSession.find(timeout=15) as bl:
            self.update_progress.emit(60, "Erasing flash…")
            bl.write_flash(firmware_data, progress_cb=_progress)
            self.update_progress.emit(92, "Rebooting device…")
            bl.reset()

    def flash_from_file(self, path: str) -> None:
        """Flash a local .bin via BootloaderSession (NXP blhost USB-HID protocol)."""
        self.update_started.emit()
        try:
            # 1. Read + sanity-check file
            self.update_progress.emit(5, f"Reading {os.path.basename(path)}…")
            try:
                with open(path, "rb") as f:
                    firmware_data = f.read()
            except OSError as e:
                self.update_completed.emit(False, f"Cannot read file: {e}")
                return
            if not self.verify_firmware(firmware_data):
                return

            # 2. Reboot, flash, reset
            self._flash_firmware_bytes(firmware_data)
            self.update_progress.emit(100, "Done.")
            self.update_completed.emit(True, "Firmware flashed successfully!")

        except BootloaderError as e:
            self.update_completed.emit(False, f"Bootloader error: {e}")
        except Exception as e:
            self.update_completed.emit(False, f"Flash failed: {e}")

    def factory_reset(self, confirm: bool = False) -> None:
        """Perform factory reset of the device."""
        if not confirm:
            self.error_occurred.emit("Factory reset requires confirmation")
            return

        try:
            self.update_progress.emit(10, "Performing factory reset...")

            if not self._device_path:
                self.error_occurred.emit("Device not connected")
                return

            # Solo2 factory reset uses CTAP2 authenticatorReset command
            # This requires user presence (touch)
            try:
                self.update_progress.emit(30, "Touch your device to confirm reset...")

                # Open fresh CTAP2 connection for reset
                from fido2.ctap2 import Ctap2
                hid_dev = self._open_hid_device()
                ctap2 = Ctap2(hid_dev)

                # Send authenticatorReset (requires user presence within 10 seconds)
                ctap2.reset()

                self.update_progress.emit(100, "Factory reset completed")
                self.update_completed.emit(True, "Device reset to factory settings")

            except Exception as e:
                if "CTAP2_ERR" in str(e) or "timeout" in str(e).lower():
                    self.error_occurred.emit(
                        "Factory reset requires touching the device within 10 seconds"
                    )
                else:
                    raise

        except Exception as e:
            self.error_occurred.emit(f"Factory reset failed: {e}")


class FirmwareRepo:
    """Firmware repository for Solo2 releases."""

    SOLOKEYS_API = "https://api.github.com/repos/solokeys/solo2/releases/latest"

    @staticmethod
    def get_latest_release_info() -> Optional[FirmwareInfo]:
        """Get latest firmware release information from GitHub."""
        try:
            headers = {"Accept": "application/vnd.github.v3+json"}
            response = requests.get(FirmwareRepo.SOLOKEYS_API, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Parse release information
            tag_name = data.get("tag_name", "")
            version = tag_name.lstrip("v") if tag_name else "Unknown"
            published_at = data.get("published_at", "")[:10]
            body = data.get("body", "")

            # Find firmware binary asset
            assets = data.get("assets", [])
            firmware_asset = None

            for asset in assets:
                name = asset.get("name", "").lower()
                # Look for firmware binary (various naming conventions)
                if any(
                    pattern in name
                    for pattern in [".bin", "firmware", "solo2"]
                    if not name.endswith(".sig")
                ):
                    firmware_asset = asset
                    break

            if not firmware_asset:
                return None

            # Try to extract checksum from release notes
            checksum = ""
            for line in body.split("\n"):
                if "sha256" in line.lower() or "checksum" in line.lower():
                    # Try to extract hash (64 hex chars)
                    import re

                    match = re.search(r"[a-fA-F0-9]{64}", line)
                    if match:
                        checksum = match.group(0)
                        break

            return FirmwareInfo(
                version=version,
                build_date=published_at,
                size=firmware_asset.get("size", 0),
                checksum=checksum,
                release_notes=body,
                download_url=firmware_asset.get("browser_download_url", ""),
            )

        except requests.exceptions.RequestException:
            return None
        except Exception:
            return None
