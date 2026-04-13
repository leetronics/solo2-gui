"""USB device monitoring for SoloKeys GUI."""

import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

from solo2.discovery import DeviceWatcher, list_presence_ids


class USBMonitor(QObject):
    """Monitors SoloKeys device hot-plug events.

    On Linux/macOS uses ``DeviceWatcher`` (fido2 HID enumeration is lightweight).
    On Windows uses ``list_presence_ids()`` (hidapi ``hid.enumerate``) which reads
    VID/PID/path via SetupAPI without opening a data connection — safe to call
    while a CTAP session is active on the same device.

    Signals carry a stable device_id string (e.g. 'hid:…').
    The bus/address integers are kept for API compatibility but are always 0.
    """

    device_connected = Signal(str, int, int)  # device_id, bus (unused), address (unused)
    device_disconnected = Signal(str, int, int)

    def __init__(self, vid: int = 0, pids: list = None, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: threading.Thread = None

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
        if sys.platform == "win32":
            self._monitor_loop_presence()
        else:
            self._monitor_loop_watcher()

    def _monitor_loop_watcher(self) -> None:
        """Linux/macOS: use DeviceWatcher (full descriptors, lightweight on these platforms)."""
        watcher = DeviceWatcher()
        while self._running:
            try:
                added, removed = watcher.poll()
                for desc in added:
                    self.device_connected.emit(desc.id, 0, 0)
                for desc in removed:
                    self.device_disconnected.emit(desc.id, 0, 0)
            except Exception:
                pass
            time.sleep(0.5)

    def _monitor_loop_presence(self) -> None:
        """Windows: use lightweight hidapi presence check (no HID data handles)."""
        previous: set[str] = set()
        try:
            previous = list_presence_ids()
        except Exception:
            pass
        while self._running:
            try:
                current = list_presence_ids()
                for device_id in current - previous:
                    self.device_connected.emit(device_id, 0, 0)
                for device_id in previous - current:
                    self.device_disconnected.emit(device_id, 0, 0)
                previous = current
            except Exception:
                pass
            time.sleep(0.5)
