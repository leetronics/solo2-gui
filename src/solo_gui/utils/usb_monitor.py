"""USB device monitoring for SoloKeys GUI."""

import threading
import time

from PySide6.QtCore import QObject, Signal

from solo2.discovery import DeviceWatcher


class USBMonitor(QObject):
    """Monitors SoloKeys device hot-plug events via solo2.discovery.DeviceWatcher.

    Signals carry a stable device_id string (e.g. 'uuid:…' or 'hid:…').
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
        """Main monitoring loop using DeviceWatcher."""
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
