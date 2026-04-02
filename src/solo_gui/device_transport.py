"""Pure-Python synchronous HID transport for native_host.py (no Qt, no threading).

Used by native_host.py when solokeys-gui is not running (direct HID path).
Mirrors the relevant portions of device_manager.py without any Qt dependency.
"""

import os
import struct
import time

from fido2.hid import CtapHidDevice, CTAPHID
from fido2.ctap2 import Ctap2


class DeviceTransport:
    """Synchronous HID transport wrapping fido2 Ctap2.

    Usage::

        with DeviceTransport() as t:
            response = t.send_apdu(apdu_bytes)

    Or use the module-level helper::

        response = call_device_apdu(apdu_bytes)
    """

    def __init__(self):
        self._ctap2 = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the first available SoloKeys HID device."""
        for hid_dev in CtapHidDevice.list_devices():
            try:
                self._ctap2 = Ctap2(hid_dev)
                return
            except Exception:
                continue
        raise RuntimeError("No SoloKeys device found")

    def close(self) -> None:
        self._ctap2 = None

    # ------------------------------------------------------------------

    def send_apdu(self, apdu_bytes: bytes, retries: int = 2) -> bytes:
        """Send a CTAPHID vendor command 0x70 (browser APDU) and return the response."""
        last_err = None
        for attempt in range(retries + 1):
            try:
                return bytes(self._ctap2.device.call(0x70, bytes(apdu_bytes)))
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                # CTAP2_ERR_CHANNEL_BUSY (0x06) or similar channel errors
                if attempt < retries and ("busy" in err_str or "channel" in err_str or "0x06" in err_str):
                    time.sleep(0.1 * (attempt + 1))
                    self._reset_channel()
                    continue
                break
        raise RuntimeError(f"APDU failed: {last_err}")

    def _reset_channel(self) -> None:
        """Reset the CTAPHID channel (mirrors device_manager._reopen_device)."""
        if self._ctap2 is None:
            return
        try:
            hid_dev = self._ctap2.device
            hid_dev._channel_id = 0xFFFFFFFF  # Broadcast channel
            nonce = os.urandom(8)
            response = hid_dev.call(CTAPHID.INIT, nonce)
            if response[:8] == nonce:
                (hid_dev._channel_id,) = struct.unpack_from(">I", response, 8)
        except Exception:
            pass


def call_device_apdu(apdu_bytes: bytes) -> bytes:
    """Single-shot convenience: open device, send APDU, close. Raises on error."""
    with DeviceTransport() as t:
        return t.send_apdu(apdu_bytes)
