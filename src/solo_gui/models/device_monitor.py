"""Device manager for SoloKeys GUI."""

import logging
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..utils.usb_monitor import USBMonitor
from solo2 import DeviceInfo, DeviceMode, DeviceStatus, Solo2Descriptor, Solo2Device, SoloDevice
from solo2.discovery import list_bootloader_descriptors, list_regular_descriptors

_log = logging.getLogger("solo2device")

try:
    from solo2.bootloader import (
        BOOTLOADER_PID,
        NXP_BOOTLOADER_PID,
        NXP_BOOTLOADER_VID,
        SOLOKEYS_VID,
        hid as bootloader_hid,
    )
except Exception:
    BOOTLOADER_PID = Solo2Device.BOOTLOADER_PID
    NXP_BOOTLOADER_PID = None
    NXP_BOOTLOADER_VID = None
    SOLOKEYS_VID = Solo2Device.SOLOKEYS_VID
    bootloader_hid = None


class _BootloaderPlaceholderDevice(SoloDevice):
    """Minimal bootloader-mode device used when only HID bootloader discovery works."""

    def connect(self) -> bool:
        self.status = DeviceStatus.CONNECTED
        return True

    def disconnect(self) -> None:
        self.status = DeviceStatus.DISCONNECTED

    def get_info(self) -> DeviceInfo:
        return DeviceInfo(path=self.path, mode=DeviceMode.BOOTLOADER, firmware_version="Bootloader")

    def is_alive(self) -> bool:
        return self.status == DeviceStatus.CONNECTED


def _list_bootloader_descriptors_for_monitor() -> List[Solo2Descriptor]:
    """Discover bootloader devices using the same fallback strategy as flashing."""
    descriptors = list_bootloader_descriptors()
    if descriptors:
        return descriptors
    if bootloader_hid is None:
        return []

    fallback: List[Solo2Descriptor] = []
    for info in bootloader_hid.enumerate():
        vid = info.get("vendor_id")
        pid = info.get("product_id")
        if (vid, pid) not in (
            (SOLOKEYS_VID, BOOTLOADER_PID),
            (NXP_BOOTLOADER_VID, NXP_BOOTLOADER_PID),
        ):
            continue
        hid_path = info.get("path")
        if hid_path is None:
            continue
        stable_id = f"bootloader-hid:{hid_path!r}"
        fallback.append(
            Solo2Descriptor(
                id=stable_id,
                mode=DeviceMode.BOOTLOADER,
                path=stable_id,
                transport="bootloader-hid",
                hid_path=hid_path,
            )
        )
    return fallback


class DeviceMonitor(QObject):
    """Monitors SoloKeys device connections and disconnections."""

    device_connected = Signal(SoloDevice)
    device_disconnected = Signal(str)  # device path
    device_error = Signal(str, str)  # device path, error message

    def __init__(self):
        super().__init__()
        self._devices: Dict[str, SoloDevice] = {}
        self._usb_monitor: Optional[USBMonitor] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_devices)
        self._missing_scans: Dict[str, int] = {}
        self._disconnect_grace_scans = 3 if sys.platform == "win32" else 1

    def _update_polling_state(self) -> None:
        """Keep the poll timer running once monitoring is active.

        With no connected devices we do a full discovery pass so newly plugged
        tokens can be opened and tracked. While a device is already connected,
        the timer switches to a lightweight presence check that only verifies
        existing device IDs are still discoverable. This keeps Linux disconnect
        detection working without re-opening devices in the background.
        """
        if self._usb_monitor is not None and not self._poll_timer.isActive():
            _log.debug(
                "_update_polling_state starting background polling"
            )
            self._poll_timer.start()

    def _poll_devices(self) -> None:
        """Run the appropriate periodic scan for the current connection state."""
        if self._devices:
            self._check_tracked_devices_present()
        else:
            self._scan_devices()

    def _current_descriptor_ids(self) -> set[str]:
        """Return currently discoverable SoloKeys descriptor IDs."""
        found_regular = list_regular_descriptors()
        found_bootloader = _list_bootloader_descriptors_for_monitor()
        _log.debug("_current_descriptor_ids found_regular=%s", [desc.id for desc in found_regular])
        _log.debug(
            "_current_descriptor_ids found_bootloader=%s",
            [desc.id for desc in found_bootloader],
        )

        current_ids = {desc.id for desc in found_regular}
        current_ids.update(desc.id for desc in found_bootloader)
        return current_ids

    def _check_tracked_devices_present(self) -> None:
        """Disconnect tracked devices that are no longer discoverable."""
        current_ids = self._current_descriptor_ids()

        for device_id in current_ids:
            self._missing_scans.pop(device_id, None)

        for device_id in list(self._devices.keys()):
            if device_id in current_ids:
                continue

            misses = self._missing_scans.get(device_id, 0) + 1
            self._missing_scans[device_id] = misses
            if misses < self._disconnect_grace_scans:
                _log.debug(
                    "_check_tracked_devices_present delaying disconnect device_id=%s misses=%d/%d",
                    device_id,
                    misses,
                    self._disconnect_grace_scans,
                )
                continue

            device = self._devices.pop(device_id)
            self._missing_scans.pop(device_id, None)
            device.disconnect()
            self.device_disconnected.emit(getattr(device, "path", device_id))

    def start_monitoring(self) -> None:
        """Start monitoring for device changes."""
        # Create USB monitor for SoloKeys devices
        self._usb_monitor = USBMonitor(
            vid=Solo2Device.SOLOKEYS_VID,
            pids=[Solo2Device.REGULAR_PID, Solo2Device.BOOTLOADER_PID],
        )

        # Connect signals
        self._usb_monitor.device_connected.connect(self._on_usb_device_connected)
        self._usb_monitor.device_disconnected.connect(self._on_usb_device_disconnected)

        # Start monitoring
        self._usb_monitor.start()

        # Initial scan, then keep polling every second on the main thread
        self._scan_devices()
        self._update_polling_state()

    def stop_monitoring(self) -> None:
        """Stop monitoring for device changes."""
        self._poll_timer.stop()
        self._missing_scans.clear()
        if self._usb_monitor:
            self._usb_monitor.stop()
            self._usb_monitor = None

    def prepare_for_expected_reconnect(self) -> None:
        """Drop tracked device objects before an expected reboot/reconnect.

        This forces the next discovery pass to create a fresh SoloDevice even if
        Windows reuses the same HID path or the disconnect notification is
        missed during a fast reboot cycle.
        """
        for device in self._devices.values():
            try:
                device.disconnect()
            except Exception:
                pass
        self._devices.clear()
        self._missing_scans.clear()
        self._update_polling_state()

    def _on_usb_device_connected(self, device_id: str, bus: int, address: int) -> None:
        """Handle USB device connection."""
        try:
            # Run a full scan now and again shortly afterwards. Windows can emit
            # the USB arrival event before the new mode is fully discoverable.
            self._scan_devices()
            QTimer.singleShot(750, self._scan_devices)
            QTimer.singleShot(1500, self._scan_devices)
        except Exception:
            pass

    def _on_usb_device_disconnected(
        self, device_id: str, bus: int, address: int
    ) -> None:
        """Handle USB device disconnection."""
        device = self._devices.get(device_id)
        if not device:
            return

        # Actually disconnect
        self._devices.pop(device_id, None)
        self._missing_scans.pop(device_id, None)
        device.disconnect()
        self.device_disconnected.emit(getattr(device, "path", device_id))
        self._update_polling_state()

    def _scan_devices(self) -> None:
        """Scan for connected SoloKeys devices."""
        found_regular = list_regular_descriptors()
        found_bootloader = _list_bootloader_descriptors_for_monitor()
        _log.debug("_scan_devices found_regular=%s", [desc.id for desc in found_regular])
        _log.debug("_scan_devices found_bootloader=%s", [desc.id for desc in found_bootloader])

        current_ids = {desc.id for desc in found_regular}
        current_ids.update(desc.id for desc in found_bootloader)

        for device_id in current_ids:
            self._missing_scans.pop(device_id, None)

        # Phase 1: Disconnect devices no longer present. On Windows we require
        # several consecutive misses because CCID/SetupAPI polling can
        # transiently fail even while the token is still plugged in.
        for device_id in list(self._devices.keys()):
            if device_id in current_ids:
                continue

            misses = self._missing_scans.get(device_id, 0) + 1
            self._missing_scans[device_id] = misses
            if misses < self._disconnect_grace_scans:
                _log.debug(
                    "_scan_devices delaying disconnect device_id=%s misses=%d/%d",
                    device_id,
                    misses,
                    self._disconnect_grace_scans,
                )
                continue

            device = self._devices.pop(device_id)
            self._missing_scans.pop(device_id, None)
            device.disconnect()
            self.device_disconnected.emit(getattr(device, "path", device_id))

        # Phase 2: Connect new devices
        for descriptor in found_regular:
            if descriptor.id in self._devices:
                continue

            device = Solo2Device.from_descriptor(descriptor)
            if device.connect():
                self._devices[descriptor.id] = device
                self._missing_scans.pop(descriptor.id, None)
                self.device_connected.emit(device)

        for descriptor in found_bootloader:
            if descriptor.id in self._devices:
                continue  # Already tracked

            if descriptor.transport == "bootloader-hid":
                device = _BootloaderPlaceholderDevice(descriptor)
            else:
                device = Solo2Device.from_descriptor(descriptor)
            if device.connect():
                self._devices[descriptor.id] = device
                self._missing_scans.pop(descriptor.id, None)
                self.device_connected.emit(device)

        self._update_polling_state()

    def get_devices(self) -> List[SoloDevice]:
        """Get all connected devices."""
        return list(self._devices.values())

    def get_device(self, path: str) -> Optional[SoloDevice]:
        """Get a specific device by path."""
        device = self._devices.get(path)
        if device is not None:
            return device
        for candidate in self._devices.values():
            if getattr(candidate, "path", None) == path:
                return candidate
        return None

    def refresh_devices(self) -> None:
        """Refresh the device list."""
        self._scan_devices()
