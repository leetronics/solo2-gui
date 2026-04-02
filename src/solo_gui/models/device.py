"""Backward-compatible device model exports backed by the solo2 core package."""

from solo2.device import (
    DeviceInfo,
    DeviceMode,
    DeviceStatus,
    FirmwareCapabilities,
    Solo2Descriptor,
    Solo2Device,
    SoloDevice,
    firmware_supports_extended_applets,
    format_firmware_full,
    format_firmware_version,
)

__all__ = [
    "DeviceInfo",
    "DeviceMode",
    "DeviceStatus",
    "FirmwareCapabilities",
    "Solo2Descriptor",
    "Solo2Device",
    "SoloDevice",
    "firmware_supports_extended_applets",
    "format_firmware_full",
    "format_firmware_version",
]
