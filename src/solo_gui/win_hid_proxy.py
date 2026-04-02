"""Client-side helpers for the SoloKeys HID proxy service (Windows non-admin path).

Usage:
    from solo_gui.win_hid_proxy import is_non_admin_windows, open_pipe_device, query_service_device_present
"""

import sys
from typing import Optional

PIPE_NAME = r"\\.\pipe\solokeys-hid"


def is_non_admin_windows() -> bool:
    """Return True when running on Windows without administrator privileges."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return not bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def query_service_device_present() -> bool:
    """Short-lived pipe query; returns False if service is unreachable or device absent."""
    try:
        from multiprocessing.connection import Client
        conn = Client(PIPE_NAME, family="AF_PIPE")
        conn.send({"type": "status"})
        resp = conn.recv()
        conn.close()
        return bool(resp.get("device_present", False))
    except Exception:
        return False


class CtapPipeDevice:
    """Drop-in CtapDevice backed by the SoloKeys HID proxy service.

    Keeps a persistent pipe connection; the caller must call close() when done.
    """

    def __init__(self, conn, capabilities: int):
        self._conn = conn
        self._capabilities = capabilities

    @property
    def capabilities(self) -> int:
        return self._capabilities

    def call(self, cmd: int, data: bytes = b"", event=None, on_keepalive=None):
        self._conn.send({"type": "call", "cmd": cmd, "data": data.hex()})
        while True:
            resp = self._conn.recv()
            rtype = resp.get("type")
            if rtype == "keepalive":
                if on_keepalive:
                    on_keepalive(resp["status"])
            elif rtype == "result":
                return bytes.fromhex(resp["data"])
            else:
                if "ctap_code" in resp:
                    from fido2.ctap import CtapError
                    raise CtapError(resp["ctap_code"])
                raise Exception(resp.get("message", "service error"))

    def wink(self) -> None:
        self._conn.send({"type": "wink"})
        resp = self._conn.recv()
        if resp.get("type") == "error":
            raise Exception(resp.get("message", "wink failed"))

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def open_pipe_device() -> Optional[CtapPipeDevice]:
    """Connect to the service pipe and return a CtapPipeDevice, or None on failure."""
    try:
        from multiprocessing.connection import Client
        conn = Client(PIPE_NAME, family="AF_PIPE")
        conn.send({"type": "status"})
        resp = conn.recv()
        if not resp.get("device_present"):
            conn.close()
            return None
        return CtapPipeDevice(conn, capabilities=resp.get("capabilities", 0))
    except Exception:
        return None
