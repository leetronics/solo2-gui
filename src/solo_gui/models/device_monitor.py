"""Device manager for SoloKeys GUI."""

from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal
import usb.core

from .device import SoloDevice, Solo2Device, DeviceMode, DeviceStatus
from ..utils.usb_monitor import USBMonitor


class DeviceMonitor(QObject):
    """Monitors SoloKeys device connections and disconnections."""

    device_connected = Signal(SoloDevice)
    device_disconnected = Signal(str)  # device path
    device_error = Signal(str, str)  # device path, error message

    def __init__(self):
        super().__init__()
        self._devices: Dict[str, SoloDevice] = {}
        self._usb_monitor: Optional[USBMonitor] = None

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

        # Initial scan
        self._scan_devices()

    def stop_monitoring(self) -> None:
        """Stop monitoring for device changes."""
        if self._usb_monitor:
            self._usb_monitor.stop()
            self._usb_monitor = None

    def _on_usb_device_connected(self, device_id: str, bus: int, address: int) -> None:
        """Handle USB device connection."""
        # Determine device mode and create device
        try:
            dev = usb.core.find(bus=bus, address=address)
            if not dev:
                return

            if dev.idProduct == Solo2Device.REGULAR_PID:
                mode = DeviceMode.REGULAR
            elif dev.idProduct == Solo2Device.BOOTLOADER_PID:
                mode = DeviceMode.BOOTLOADER
            else:
                return

            # Create device if not already tracked
            if device_id not in self._devices:
                # Check if we already have a connected device of this mode
                # (USB address can change during re-enumeration)
                already_connected = any(
                    d.mode == mode and d.status == DeviceStatus.CONNECTED
                    for d in self._devices.values()
                )
                if already_connected:
                    return

                device = Solo2Device(device_id, mode)
                if device.connect():
                    self._devices[device_id] = device
                    self.device_connected.emit(device)
                # Don't emit error - silently skip devices that fail to connect

        except Exception:
            # Log error but continue
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
        current_devices = set()
        current_modes = set()  # Track which modes we found

        try:
            # Find all SoloKeys devices
            devices = usb.core.find(idVendor=Solo2Device.SOLOKEYS_VID, find_all=True)

            for dev in devices:
                device_id = f"{dev.bus}-{dev.address}"
                current_devices.add(device_id)

                # Determine device mode
                if dev.idProduct == Solo2Device.REGULAR_PID:
                    mode = DeviceMode.REGULAR
                elif dev.idProduct == Solo2Device.BOOTLOADER_PID:
                    mode = DeviceMode.BOOTLOADER
                else:
                    continue

                current_modes.add(mode)

                # Create device if not already tracked
                if device_id not in self._devices:
                    # Check if we already have a connected device of this mode
                    # (USB address can change during re-enumeration)
                    already_connected = any(
                        d.mode == mode and d.status == DeviceStatus.CONNECTED
                        for d in self._devices.values()
                    )
                    if already_connected:
                        # Don't try to reconnect - device is fine, just address changed
                        continue

                    device = Solo2Device(device_id, mode)
                    if device.connect():
                        self._devices[device_id] = device
                        self.device_connected.emit(device)
                    # Don't emit error - silently skip devices that fail to connect

        except Exception:
            # Log error but continue
            pass

        # Check for disconnected devices - but only if we didn't find any device of that mode
        # (handles USB re-enumeration where address changes but device is still there)
        disconnected = set(self._devices.keys()) - current_devices
        for device_id in disconnected:
            device = self._devices.get(device_id)
            if device and device.mode in current_modes:
                # Device mode still exists, just address changed - don't disconnect
                continue
            device = self._devices.pop(device_id, None)
            if device:
                device.disconnect()
                self.device_disconnected.emit(device_id)

    def get_devices(self) -> List[SoloDevice]:
        """Get all connected devices."""
        return list(self._devices.values())

    def get_device(self, path: str) -> Optional[SoloDevice]:
        """Get a specific device by path."""
        return self._devices.get(path)

    def refresh_devices(self) -> None:
        """Refresh the device list."""
        self._scan_devices()
