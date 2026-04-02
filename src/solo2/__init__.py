"""Core Solo 2 device library used by the GUI and native host."""

from .clients import Solo2AdminClient, Solo2FidoClient, Solo2SecretsClient
from .device import (
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
from .discovery import (
    DeviceSnapshot,
    DeviceWatcher,
    list_bootloader_descriptors,
    list_descriptors,
    list_devices,
    list_regular_descriptors,
    open_bootloader,
    open_device,
)
from .errors import Solo2Error, Solo2NotFoundError, Solo2TransportError
from .pcsc import pcsc_available, should_prefer_ccid

__all__ = [
    "DeviceInfo",
    "DeviceSnapshot",
    "DeviceMode",
    "DeviceStatus",
    "DeviceWatcher",
    "FirmwareCapabilities",
    "Solo2AdminClient",
    "Solo2Descriptor",
    "Solo2Device",
    "Solo2Error",
    "Solo2FidoClient",
    "Solo2NotFoundError",
    "Solo2SecretsClient",
    "Solo2TransportError",
    "SoloDevice",
    "firmware_supports_extended_applets",
    "format_firmware_full",
    "format_firmware_version",
    "list_bootloader_descriptors",
    "list_descriptors",
    "list_devices",
    "list_regular_descriptors",
    "open_bootloader",
    "open_device",
    "pcsc_available",
    "should_prefer_ccid",
]
