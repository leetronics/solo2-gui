"""Device manager for SoloKeys GUI."""

import logging
from typing import Dict, List, Optional

import usb.core
from PySide6.QtCore import QObject, QTimer, Signal

from ..hid_backend import list_ctap_hid_devices
from .device import DeviceMode, Solo2Device, SoloDevice
from ..utils.usb_monitor import USBMonitor

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
        self._poll_timer.start()

    def stop_monitoring(self) -> None:
        """Stop monitoring for device changes."""
        self._poll_timer.stop()
        if self._usb_monitor:
            self._usb_monitor.stop()
            self._usb_monitor = None

    def _on_usb_device_connected(self, device_id: str, bus: int, address: int) -> None:
        """Handle USB device connection."""
        try:
            dev = usb.core.find(bus=bus, address=address)
            if not dev:
                return

            if dev.idProduct == Solo2Device.REGULAR_PID:
                return
            elif dev.idProduct == Solo2Device.BOOTLOADER_PID:
                mode = DeviceMode.BOOTLOADER
            else:
                return

            if device_id not in self._devices:
                device = Solo2Device(device_id, mode)
                if device.connect():
                    self._devices[device_id] = device
                    self.device_connected.emit(device)
        except Exception:
            pass

    def _on_usb_device_disconnected(
        self, device_id: str, bus: int, address: int
    ) -> None:
        """Handle USB device disconnection."""
        device = self._devices.get(device_id)
        if not device:
            return

        # Check if there's still a USB device of the same mode connected
        # (handles USB re-enumeration where address changes)
        try:
            if device.mode == DeviceMode.REGULAR:
                pid = Solo2Device.REGULAR_PID
            else:
                pid = Solo2Device.BOOTLOADER_PID

            still_connected = usb.core.find(
                idVendor=Solo2Device.SOLOKEYS_VID, idProduct=pid
            )
            if still_connected:
                # Device still there, just address changed - don't disconnect
                return
        except Exception:
            pass

        # Actually disconnect
        self._devices.pop(device_id, None)
        device.disconnect()
        self.device_disconnected.emit(device_id)

    def _scan_devices(self) -> None:
        """Scan for connected SoloKeys devices."""
        found_regular = self._scan_regular_devices()
        _log.debug("_scan_devices found_regular=%s", found_regular)

        try:
            found_bootloader = list(
                usb.core.find(
                    idVendor=Solo2Device.SOLOKEYS_VID,
                    idProduct=Solo2Device.BOOTLOADER_PID,
                    find_all=True,
                ) or []
            )
        except Exception:
            found_bootloader = []
        _log.debug(
            "_scan_devices found_bootloader=%s",
            [f"{dev.bus}-{dev.address}" for dev in found_bootloader],
        )

        current_ids = set(found_regular)
        current_ids.update(f"{dev.bus}-{dev.address}" for dev in found_bootloader)

        # Phase 1: Disconnect devices no longer present
        for device_id in list(self._devices.keys()):
            if device_id not in current_ids:
                device = self._devices.pop(device_id)
                device.disconnect()
                self.device_disconnected.emit(device_id)

        # Phase 2: Connect new devices
        for device_id in found_regular:
            if device_id in self._devices:
                continue

            device = Solo2Device(device_id, DeviceMode.REGULAR)
            if device.connect():
                self._devices[device.path] = device
                self.device_connected.emit(device)

        for dev in found_bootloader:
            device_id = f"{dev.bus}-{dev.address}"
            if device_id in self._devices:
                continue  # Already tracked

            if dev.idProduct == Solo2Device.BOOTLOADER_PID:
                mode = DeviceMode.BOOTLOADER
            else:
                continue

            device = Solo2Device(device_id, mode)
            if device.connect():
                self._devices[device_id] = device
                self.device_connected.emit(device)

    def get_devices(self) -> List[SoloDevice]:
        """Get all connected devices."""
        return list(self._devices.values())

    def get_device(self, path: str) -> Optional[SoloDevice]:
        """Get a specific device by path."""
        return self._devices.get(path)

    def refresh_devices(self) -> None:
        """Refresh the device list."""
        self._scan_devices()

    def _scan_regular_devices(self) -> List[str]:
        """Discover regular Solo 2 devices by HID and return stable IDs."""
        tracked_regular = [d for d in self._devices.values() if d.mode == DeviceMode.REGULAR]
        _log.debug(
            "_scan_regular_devices tracked_regular=%s",
            [device.path for device in tracked_regular],
        )
        if tracked_regular:
            if self._regular_hid_present():
                return [device.path for device in tracked_regular]
            return []

        discovered: List[str] = []
        seen_ids = set()
        try:
            for hid_dev in list_ctap_hid_devices():
                desc = getattr(hid_dev, "descriptor", None)
                if not desc:
                    continue
                _log.debug(
                    "_scan_devices HID: path=%r vid=0x%04x pid=0x%04x",
                    getattr(desc, "path", None),
                    getattr(desc, "vid", 0) or 0,
                    getattr(desc, "pid", 0) or 0,
                )
                candidate = Solo2Device(f"hid:{desc.path!r}", DeviceMode.REGULAR)
                if candidate.connect():
                    _log.debug(
                        "_scan_regular_devices connected candidate original=%r stable=%s",
                        getattr(desc, "path", None),
                        candidate.path,
                    )
                    if candidate.path not in seen_ids:
                        discovered.append(candidate.path)
                        seen_ids.add(candidate.path)
        except Exception as e:
            _log.debug("_scan_regular_devices failed: %s", e)
        return discovered

    def _regular_hid_present(self) -> bool:
        """Return True while at least one HID device is still present."""
        try:
            for hid_dev in list_ctap_hid_devices():
                desc = getattr(hid_dev, "descriptor", None)
                if not desc:
                    continue
                return True
        except Exception as e:
            _log.debug("_regular_hid_present failed: %s", e)
        return False
