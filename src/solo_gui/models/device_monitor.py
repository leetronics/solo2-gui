"""Device manager for SoloKeys GUI."""

import logging
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..utils.usb_monitor import USBMonitor
from solo2 import DeviceMode, Solo2Device, SoloDevice
from solo2.discovery import list_bootloader_descriptors, list_regular_descriptors

_log = logging.getLogger("solo2device")


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
        self._poll_timer.timeout.connect(self._scan_devices)
        self._missing_scans: Dict[str, int] = {}
        self._disconnect_grace_scans = 3 if sys.platform == "win32" else 1

    def _update_polling_state(self) -> None:
        """Poll only while no device is connected.

        Once we have a device, rely on the USB monitor for hotplug events.
        This avoids reopening CCID readers in the background while the GUI
        and browser bridge are actively using the token.
        """
        if self._devices:
            if self._poll_timer.isActive():
                _log.debug(
                    "_update_polling_state stopping background polling while connected"
                )
                self._poll_timer.stop()
            return

        if self._usb_monitor is not None and not self._poll_timer.isActive():
            _log.debug(
                "_update_polling_state starting background polling with no device connected"
            )
            self._poll_timer.start()

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

    def _on_usb_device_connected(self, device_id: str, bus: int, address: int) -> None:
        """Handle USB device connection."""
        try:
            if device_id not in self._devices:
                for descriptor in list_bootloader_descriptors():
                    if descriptor.id != device_id:
                        continue
                    device = Solo2Device.from_descriptor(descriptor)
                    if device.connect():
                        self._devices[descriptor.id] = device
                        self._missing_scans.pop(descriptor.id, None)
                        self.device_connected.emit(device)
                        self._update_polling_state()
                    break
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
        found_bootloader = list_bootloader_descriptors()
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
