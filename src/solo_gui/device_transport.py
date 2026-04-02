"""Backward-compatible transport wrapper around the solo2 core package."""

from solo2.transport import DeviceTransport, call_device_apdu

__all__ = ["DeviceTransport", "call_device_apdu"]
