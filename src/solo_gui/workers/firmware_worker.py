"""Firmware update worker for SoloKeys GUI.

Solo2 firmware update process:
1. Check current firmware version via CTAP2 device info
2. Check for updates from GitHub releases
3. Download firmware binary
4. Reboot device to bootloader mode using admin app command
5. Flash firmware using bootloader protocol
6. Reboot back to regular mode
"""

from typing import Callable, Optional
from dataclasses import dataclass
import importlib.resources
import os
import time
import hashlib

from PySide6.QtCore import QObject, Signal
import requests

from solo2.admin import AdminSession, RebootMode
from solo2.bootloader import BootloaderSession, BootloaderError
from solo2.provisioner import ProvisionerSession
from solo2.errors import Solo2CommandError, Solo2TransportError


def _is_sb2_file(data: bytes) -> bool:
    """Detect SB2.1 by the 'sgtl' magic at bytes 28-32."""
    return len(data) >= 96 and data[28:32] == b"sgtl"


@dataclass
class FirmwareInfo:
    """Firmware version information."""

    version: str
    build_date: str
    size: int
    checksum: str
    release_notes: str
    download_url: str = ""   # .bin URL (Hacker)
    sb2_url: str = ""        # .sb2 URL (Secure)


class FirmwareUpdateWorker(QObject):
    """Worker thread for firmware update operations."""

    update_started = Signal()
    update_progress = Signal(int, str)  # progress, message
    update_completed = Signal(bool, str)  # success, message
    error_occurred = Signal(str)  # error message
    firmware_info_found = Signal(object)  # FirmwareInfo or None
    bootloader_mode_changed = Signal(bool)  # True if in bootloader mode

    def __init__(self, device, is_locked: Optional[bool] = None):
        super().__init__()
        self._device = device
        self._is_locked = is_locked  # True=locked, False=unlocked, None=unknown

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

            is_sb2 = _is_sb2_file(firmware_data)
            if self._is_locked is True and not is_sb2:
                self.error_occurred.emit(
                    "This device is locked — only signed SB2.1 firmware (.sb2) can be flashed.\n"
                    "A raw .bin file will not boot on this device."
                )
                return False
            if self._is_locked is None and not is_sb2:
                self.error_occurred.emit(
                    "Device lock status could not be determined.\n"
                    "Refusing to flash a raw .bin in case the device is locked.\n"
                    "Use 'Check Variant' in the Overview tab, or use a signed SB2.1 firmware file."
                )
                return False

            return True

        except Exception as e:
            self.error_occurred.emit(f"Verification failed: {e}")
            return False

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

    def _read_firmware_file(self, path: str) -> Optional[bytes]:
        """Read a local firmware file and emit a useful progress step."""
        self.update_progress.emit(5, f"Reading {os.path.basename(path)}…")
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError as exc:
            self.update_completed.emit(False, f"Cannot read file: {exc}")
            return None

    def _run_flash_flow(
        self,
        *,
        firmware_loader: Callable[[], Optional[bytes]],
        expected_hash: str = "",
        success_message: str,
        completion_label: str,
    ) -> None:
        """Shared end-to-end firmware flashing flow for all sources."""
        self.update_started.emit()

        try:
            firmware_data = firmware_loader()
            if not firmware_data:
                return
            if not self.verify_firmware(firmware_data, expected_hash):
                return

            self._flash_firmware_bytes(firmware_data)
            # Ensure device exits bootloader mode — bl.reset() inside
            # _flash_firmware_bytes may not reach the device before the HID
            # session closes, so we try once more with a fresh session.
            try:
                self.reboot_to_regular()
            except Exception:
                pass
            self.update_progress.emit(100, completion_label)
            self.update_completed.emit(True, success_message)

        except BootloaderError as exc:
            error_msg = str(exc)
            try:
                self.reboot_to_regular()
            except Exception:
                pass
            self.update_completed.emit(False, f"Bootloader error: {error_msg}")
        except Exception as exc:
            error_msg = str(exc)
            try:
                self.reboot_to_regular()
            except Exception:
                pass
            self.update_completed.emit(False, f"Flash failed: {error_msg}")

    def perform_update(self, firmware_info: FirmwareInfo) -> None:
        """Perform complete firmware update process."""
        if self._is_locked is True:
            if not firmware_info.sb2_url:
                self.update_completed.emit(
                    False,
                    "No signed SB2.1 firmware found in the latest release.\n"
                    "Cannot update a locked device with an unsigned binary."
                )
                return
            url = firmware_info.sb2_url
        elif self._is_locked is False:
            url = firmware_info.download_url or firmware_info.sb2_url
            if not url:
                self.update_completed.emit(False, "No firmware asset found in the latest release.")
                return
        else:
            if not firmware_info.sb2_url:
                self.update_completed.emit(
                    False,
                    "Device lock status could not be determined.\n"
                    "Refusing automatic update because no signed SB2.1 firmware asset is available."
                )
                return
            url = firmware_info.sb2_url
            self.update_progress.emit(
                0,
                "Device lock status is unknown; using signed SB2.1 firmware as a safe default.",
            )
            if not url:
                self.update_completed.emit(False, "No firmware asset found in the latest release.")
                return

        self._run_flash_flow(
            firmware_loader=lambda: self.download_firmware(url),
            expected_hash=firmware_info.checksum,
            success_message="Firmware updated successfully!",
            completion_label="Update complete!",
        )

    # ------------------------------------------------------------------ flash_from_file

    def _flash_firmware_bytes(self, firmware_data: bytes) -> None:
        """Flash firmware bytes via the MCU bootloader HID protocol."""
        size_kb = len(firmware_data) // 1024
        use_sb2 = _is_sb2_file(firmware_data)
        fmt = "SB2.1 (signed)" if use_sb2 else "raw binary"
        self.update_progress.emit(48, f"Firmware ready: {size_kb} KB ({fmt})")

        self.update_progress.emit(50, "Sending reboot-to-bootloader command…")
        try:
            AdminSession(self._device).reboot(RebootMode.BOOTLOADER)
            self.update_progress.emit(
                52,
                "Reboot command accepted — press the Solo 2 button now if it is blinking",
            )
        except Exception:
            self.update_progress.emit(52, "Reboot command sent (no confirmation)")

        self.update_progress.emit(
            54,
            "Waiting for bootloader — press the Solo 2 button now if it is asking for touch",
        )
        time.sleep(2.0)

        self.update_progress.emit(
            56,
            "Searching for bootloader device — keep the Solo 2 connected",
        )

        def _progress(written: int, total_bytes: int) -> None:
            pct = 65 + int(written / total_bytes * 25)
            self.update_progress.emit(
                pct, f"Writing: {written // 1024}/{total_bytes // 1024} KB"
            )

        def _erase_progress(erased: int, total_bytes: int) -> None:
            pct = 62 + int(erased / total_bytes * 3)
            self.update_progress.emit(
                pct, f"Erasing: {erased // 1024}/{total_bytes // 1024} KB"
            )

        with BootloaderSession.find(timeout=15) as bl:
            self.update_progress.emit(60, "Bootloader connected")
            if use_sb2:
                self.update_progress.emit(62, "Sending SB2.1 signed firmware…")
                bl.receive_sb_file(firmware_data, progress_cb=_progress)
            else:
                self.update_progress.emit(62, "Erasing flash…")
                bl.write_flash(firmware_data, progress_cb=_progress, erase_progress_cb=_erase_progress)
            self.update_progress.emit(92, "Write complete — sending reset command…")
            time.sleep(0.5)  # give bootloader time to finalize before reset
            bl.reset()

    def flash_from_file(self, path: str) -> None:
        """Flash a local .bin via BootloaderSession (NXP blhost USB-HID protocol)."""
        self._run_flash_flow(
            firmware_loader=lambda: self._read_firmware_file(path),
            success_message="Firmware flashed successfully!",
            completion_label="Done.",
        )

    # ----------------------------------------------- attestation provisioning

    def _load_bundled_provisioner(self) -> Optional[bytes]:
        """Load the bundled provisioner firmware binary from package resources."""
        # Try importlib.resources first (works in installed packages + PyInstaller)
        try:
            ref = importlib.resources.files("solo_gui") / "resources" / "provisioner-minimal.bin"
            return ref.read_bytes()
        except Exception:
            pass

        # Fallback: PyInstaller _MEIPASS
        try:
            import sys
            base = getattr(sys, "_MEIPASS", None)
            if base:
                path = os.path.join(base, "resources", "provisioner-minimal.bin")
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        return f.read()
        except Exception:
            pass

        # Fallback: relative to this file (development)
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(here, "..", "resources", "provisioner-minimal.bin")
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    return f.read()
        except Exception:
            pass

        return None

    def _connect_provisioner(self, timeout: float = 20.0) -> ProvisionerSession:
        """Wait for a provisioner applet to appear on PC/SC and connect.

        The device needs time to boot after flashing the provisioner firmware.
        We retry the PC/SC scan until the applet responds or we time out.
        """
        deadline = time.monotonic() + timeout
        last_error = "Timeout waiting for provisioner"

        while time.monotonic() < deadline:
            try:
                session = ProvisionerSession(device=None)
                session._connect_pcsc()
                return session
            except (Solo2TransportError, Solo2CommandError) as exc:
                last_error = str(exc)
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1.0)

        raise Solo2TransportError(f"Provisioner not found: {last_error}")

    def _reboot_to_bootloader_fresh(self) -> None:
        """Discover the device anew and send admin reboot-to-bootloader.

        After flashing provisioner firmware, the original self._device handle
        is stale (different USB session). We re-discover via solo2.discovery.
        """
        from solo2.discovery import list_regular_descriptors, open_device

        self.update_progress.emit(78, "Scanning for device after provisioning…")

        deadline = time.monotonic() + 15.0
        device = None
        while time.monotonic() < deadline:
            try:
                descriptors = list_regular_descriptors()
                if descriptors:
                    device = open_device(descriptors[0])
                    break
            except Exception:
                pass
            time.sleep(1.0)

        if device is None:
            raise RuntimeError("Device not found after provisioning — is it still connected?")

        self.update_progress.emit(80, "Sending reboot-to-bootloader command…")
        try:
            AdminSession(device).reboot(RebootMode.BOOTLOADER)
            self.update_progress.emit(
                82,
                "Reboot command accepted — press the Solo 2 button now if it is blinking",
            )
        except Exception:
            self.update_progress.emit(82, "Reboot command sent (no confirmation)")

        time.sleep(2.0)

    def flash_from_file_with_attestation(self, path: str) -> None:
        """Flash firmware with FIDO2 self-attestation provisioning.

        6-phase workflow:
        1. Read & verify target firmware file
        2. Generate attestation key material in memory
        3. Flash bundled provisioner firmware
        4. Wait & connect to provisioner via PC/SC
        5. Provision key + cert via write_file
        6. Flash the user's target firmware
        """
        from solo_gui.utils.attestation import generate_fido_attestation

        self.update_started.emit()

        try:
            # --- Phase 1: Read and verify target firmware ---
            self.update_progress.emit(2, "Phase 1/6: Reading target firmware…")
            target_data = self._read_firmware_file(path)
            if not target_data:
                return
            if not self.verify_firmware(target_data):
                return

            # --- Phase 2: Generate attestation key material ---
            self.update_progress.emit(10, "Phase 2/6: Generating FIDO2 attestation key…")
            try:
                key_blob, cert_der = generate_fido_attestation()
                self.update_progress.emit(
                    12,
                    f"Attestation key generated: key={len(key_blob)}B, cert={len(cert_der)}B",
                )
            except Exception as exc:
                self.update_completed.emit(False, f"Key generation failed: {exc}")
                return

            # --- Phase 3: Flash provisioner firmware ---
            self.update_progress.emit(15, "Phase 3/6: Loading provisioner firmware…")
            provisioner_data = self._load_bundled_provisioner()
            if not provisioner_data:
                self.update_completed.emit(
                    False,
                    "Bundled provisioner firmware not found.\n"
                    "The GUI installation may be incomplete.",
                )
                return

            self.update_progress.emit(
                18,
                f"Provisioner firmware: {len(provisioner_data) // 1024} KB",
            )

            # Flash provisioner (reuses existing _flash_firmware_bytes)
            try:
                self._flash_firmware_bytes(provisioner_data)
            except Exception as exc:
                self.update_completed.emit(
                    False, f"Failed to flash provisioner: {exc}"
                )
                return

            # Wait for provisioner to boot
            self.update_progress.emit(60, "Phase 4/6: Waiting for provisioner to boot…")
            time.sleep(4.0)

            # --- Phase 4: Connect to provisioner via PC/SC ---
            provisioning_ok = False
            try:
                self.update_progress.emit(64, "Connecting to provisioner via PC/SC…")
                prov = self._connect_provisioner(timeout=20.0)
                self.update_progress.emit(68, "Provisioner connected!")

                # --- Phase 5: Write key + cert ---
                self.update_progress.emit(70, "Phase 5/6: Writing attestation key…")
                prov.write_file("/fido/sec/00", key_blob)
                self.update_progress.emit(73, "Attestation key written to /fido/sec/00")

                # Reconnect (write_file closes the session)
                prov = self._connect_provisioner(timeout=10.0)
                self.update_progress.emit(74, "Writing attestation certificate…")
                prov.write_file("/fido/x5c/00", cert_der)
                self.update_progress.emit(76, "Attestation cert written to /fido/x5c/00")

                provisioning_ok = True

            except Exception as exc:
                self.update_progress.emit(
                    76,
                    f"WARNING: Provisioning failed: {exc}\n"
                    "Will still flash target firmware to avoid leaving provisioner on device.",
                )

            # --- Phase 6: Flash target firmware ---
            self.update_progress.emit(78, "Phase 6/6: Preparing to flash target firmware…")
            try:
                self._reboot_to_bootloader_fresh()
            except Exception as exc:
                # If we can't discover the device, the user may need to manually
                # enter bootloader mode (hold button while plugging in).
                self.update_progress.emit(
                    80,
                    f"Could not auto-reboot: {exc}\n"
                    "Trying to find bootloader directly…",
                )

            self.update_progress.emit(84, "Searching for bootloader…")
            try:
                self._flash_firmware_bytes_bootloader_only(target_data)
            except Exception as exc:
                msg = f"Failed to flash target firmware: {exc}"
                if provisioning_ok:
                    msg += "\nAttestation keys were provisioned successfully before this error."
                self.update_completed.emit(False, msg)
                return

            # Reboot to regular mode
            try:
                self.reboot_to_regular()
            except Exception:
                pass

            if provisioning_ok:
                self.update_progress.emit(100, "Done — firmware flashed with attestation!")
                self.update_completed.emit(
                    True,
                    "Firmware flashed with FIDO2 self-attestation!",
                )
            else:
                self.update_progress.emit(100, "Done — firmware flashed (attestation failed)")
                self.update_completed.emit(
                    False,
                    "Target firmware was flashed, but attestation provisioning failed.\n"
                    "NFC FIDO2 may not work. You can retry the attestation provisioning.",
                )

        except BootloaderError as exc:
            try:
                self.reboot_to_regular()
            except Exception:
                pass
            self.update_completed.emit(False, f"Bootloader error: {exc}")
        except Exception as exc:
            try:
                self.reboot_to_regular()
            except Exception:
                pass
            self.update_completed.emit(False, f"Attestation flash failed: {exc}")

    def _flash_firmware_bytes_bootloader_only(self, firmware_data: bytes) -> None:
        """Flash firmware via bootloader — assumes device is already in or entering bootloader.

        Unlike _flash_firmware_bytes, this does NOT send the admin reboot command
        (since the device may be running provisioner firmware, not the admin app).
        """
        use_sb2 = _is_sb2_file(firmware_data)

        def _progress(written: int, total_bytes: int) -> None:
            pct = 86 + int(written / total_bytes * 10)
            self.update_progress.emit(
                pct, f"Writing: {written // 1024}/{total_bytes // 1024} KB"
            )

        def _erase_progress(erased: int, total_bytes: int) -> None:
            pct = 84 + int(erased / total_bytes * 2)
            self.update_progress.emit(
                pct, f"Erasing: {erased // 1024}/{total_bytes // 1024} KB"
            )

        with BootloaderSession.find(timeout=15) as bl:
            self.update_progress.emit(85, "Bootloader connected — flashing target firmware")
            if use_sb2:
                bl.receive_sb_file(firmware_data, progress_cb=_progress)
            else:
                bl.write_flash(firmware_data, progress_cb=_progress, erase_progress_cb=_erase_progress)
            self.update_progress.emit(96, "Write complete — sending reset…")
            time.sleep(0.5)
            bl.reset()

    def factory_reset(self, confirm: bool = False) -> None:
        """Perform factory reset of the device."""
        if not confirm:
            self.error_occurred.emit("Factory reset requires confirmation")
            return

        try:
            self.update_progress.emit(10, "Performing factory reset...")

            if not self._device:
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

            # Find firmware binary assets (.bin for Hacker, .sb2 for Secure)
            assets = data.get("assets", [])
            bin_asset = None
            sb2_asset = None

            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".sig"):
                    continue
                if name.endswith(".sb2") and sb2_asset is None:
                    sb2_asset = asset
                elif name.endswith(".bin") and bin_asset is None:
                    bin_asset = asset

            primary = sb2_asset or bin_asset
            if not primary:
                return None

            # Try to extract checksum from release notes
            checksum = ""
            for line in body.split("\n"):
                if "sha256" in line.lower() or "checksum" in line.lower():
                    import re

                    match = re.search(r"[a-fA-F0-9]{64}", line)
                    if match:
                        checksum = match.group(0)
                        break

            return FirmwareInfo(
                version=version,
                build_date=published_at,
                size=primary.get("size", 0),
                checksum=checksum,
                release_notes=body,
                download_url=bin_asset.get("browser_download_url", "") if bin_asset else "",
                sb2_url=sb2_asset.get("browser_download_url", "") if sb2_asset else "",
            )

        except requests.exceptions.RequestException:
            return None
        except Exception:
            return None
