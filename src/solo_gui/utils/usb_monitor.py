"""USB device monitoring for SoloKeys GUI."""

import threading
import time
from typing import Callable, Set, Dict
from PySide6.QtCore import QObject, Signal

import usb.core


class USBMonitor(QObject):
    """Monitors USB device hot-plug events."""

    device_connected = Signal(str, int, int)  # bus, address, vid, pid
    device_disconnected = Signal(str, int, int)

    def __init__(self, vid: int, pids: list[int]):
        super().__init__()
        self.vid = vid
        self.pids = pids
        self._running = False
        self._thread: threading.Thread = None
        self._connected_devices: Set[str] = set()

    def start(self) -> None:
        """Start monitoring USB devices."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring USB devices."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                current_devices = self._scan_devices()

                # Find new devices
                new_devices = current_devices - self._connected_devices
                for device_id in new_devices:
                    bus, address = map(int, device_id.split("-"))
                    self.device_connected.emit(device_id, bus, address)

                # Find disconnected devices
                removed_devices = self._connected_devices - current_devices
                for device_id in removed_devices:
                    bus, address = map(int, device_id.split("-"))
                    self.device_disconnected.emit(device_id, bus, address)

                self._connected_devices = current_devices

            except Exception:
                # Ignore errors and continue monitoring
                pass

            time.sleep(0.5)  # Check every 500ms

    def _scan_devices(self) -> Set[str]:
        """Scan for connected SoloKeys devices."""
        devices = set()

        try:
            # Find all devices with matching VID/PID
            for device in usb.core.find(find_all=True, idVendor=self.vid):
                if device.idProduct in self.pids:
                    device_id = f"{device.bus}-{device.address}"
                    devices.add(device_id)

        except Exception:
            # Ignore USB errors
            pass

        return devices
