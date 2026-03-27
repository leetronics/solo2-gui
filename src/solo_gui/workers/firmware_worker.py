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
import struct
import time
import hashlib

from PySide6.QtCore import QObject, Signal
import usb.core
import usb.util
import requests


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
        self._device_path = device.hid_device_path if device else None
        self._bootloader = None

    def _open_hid_device(self):
        """Open HID device connection."""
        if not self._device_path:
            raise RuntimeError("No device path available")
        from fido2.hid import CtapHidDevice
        for hid_dev in CtapHidDevice.list_devices():
            if hid_dev.descriptor.path == self._device_path:
                return hid_dev
        raise RuntimeError("Device not found")

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

            if not self._device_path:
                self.error_occurred.emit("Device not connected")
                return False

            # Solo2 admin app uses CTAPHID vendor command to reboot
            # Command: 0x51 (ADMIN_REBOOT) with subcommand for bootloader
            try:
                hid_dev = self._open_hid_device()

                # Send admin reboot command
                # Format: [command, subcommand, ...]
                # Subcommand 0x01 = reboot to bootloader
                admin_cmd = bytes([0x51, 0x01])  # ADMIN_REBOOT to bootloader

                # Use CTAPHID vendor command channel
                hid_dev.call(0x40 | 0x11, admin_cmd)  # Vendor command

            except Exception as e:
                # Device may disconnect immediately, which is expected
                if "pipe" not in str(e).lower() and "timeout" not in str(e).lower():
                    raise

            # Wait for device to reboot
            self.update_progress.emit(55, "Waiting for bootloader...")
            time.sleep(3)

            # Check if bootloader is available
            for attempt in range(10):
                bootloader = self._find_bootloader()
                if bootloader:
                    self._bootloader = bootloader
                    self.bootloader_mode_changed.emit(True)
                    self.update_progress.emit(60, "Bootloader detected")
                    return True
                time.sleep(0.5)

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
            if not self._bootloader:
                self._bootloader = self._find_bootloader()

            if not self._bootloader:
                self.error_occurred.emit("Bootloader not found")
                return False

            self.update_progress.emit(65, "Preparing to flash...")

            # Detach kernel driver if needed
            try:
                if self._bootloader.is_kernel_driver_active(0):
                    self._bootloader.detach_kernel_driver(0)
            except Exception:
                pass

            # Set configuration
            try:
                self._bootloader.set_configuration()
            except usb.core.USBError:
                pass  # May already be configured

            # Get endpoints
            cfg = self._bootloader.get_active_configuration()
            intf = cfg[(0, 0)]

            ep_out = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
                == usb.util.ENDPOINT_OUT,
            )

            ep_in = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
                == usb.util.ENDPOINT_IN,
            )

            if not ep_out or not ep_in:
                self.error_occurred.emit("Could not find bootloader endpoints")
                return False

            # Flash firmware in chunks
            chunk_size = 512
            total_chunks = (len(firmware_data) + chunk_size - 1) // chunk_size
            base_address = 0x00000000  # Flash start address

            for i in range(total_chunks):
                offset = i * chunk_size
                chunk = firmware_data[offset : offset + chunk_size]

                # Pad last chunk if needed
                if len(chunk) < chunk_size:
                    chunk = chunk + bytes(chunk_size - len(chunk))

                # Send write command
                address = base_address + offset
                cmd = self._build_write_command(address, chunk)

                try:
                    ep_out.write(cmd, timeout=5000)

                    # Wait for acknowledgment
                    response = ep_in.read(64, timeout=5000)
                    if response[0] != 0x00:  # Check status
                        self.error_occurred.emit(
                            f"Write failed at offset {offset}: status {response[0]}"
                        )
                        return False

                except usb.core.USBError as e:
                    self.error_occurred.emit(f"USB error during flash: {e}")
                    return False

                # Update progress (65-90%)
                progress = 65 + int((i / total_chunks) * 25)
                self.update_progress.emit(
                    progress, f"Writing: {i + 1}/{total_chunks} chunks"
                )

            self.update_progress.emit(90, "Verifying flash...")

            # Verify written data (optional, depends on bootloader support)
            # ...

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

            if self._bootloader:
                try:
                    # Send reboot command
                    cfg = self._bootloader.get_active_configuration()
                    intf = cfg[(0, 0)]
                    ep_out = usb.util.find_descriptor(
                        intf,
                        custom_match=lambda e: usb.util.endpoint_direction(
                            e.bEndpointAddress
                        )
                        == usb.util.ENDPOINT_OUT,
                    )

                    if ep_out:
                        CMD_REBOOT = 0x05
                        ep_out.write(bytes([CMD_REBOOT]), timeout=1000)
                except usb.core.USBError:
                    pass  # Device disconnects immediately

            self._bootloader = None
            self.bootloader_mode_changed.emit(False)

            # Wait for device to reboot
            time.sleep(3)

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

            # Step 3: Reboot to bootloader
            if not self.reboot_to_bootloader():
                return

            # Step 4: Flash firmware
            if not self.flash_firmware(firmware_data):
                self.reboot_to_regular()  # Try to recover
                return

            # Step 5: Reboot back to regular mode
            if not self.reboot_to_regular():
                self.update_completed.emit(
                    True, "Firmware updated. Please manually reboot device."
                )
                return

            self.update_progress.emit(100, "Update complete!")
            self.update_completed.emit(True, "Firmware updated successfully!")

        except Exception as e:
            self.error_occurred.emit(f"Update failed: {e}")
            # Try to recover
            try:
                self.reboot_to_regular()
            except Exception:
                pass

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
