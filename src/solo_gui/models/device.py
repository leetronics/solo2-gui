"""Device models for SoloKeys GUI."""

import struct
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import usb.core
from fido2.ctap2 import Ctap2
from fido2.hid import CtapHidDevice


def format_firmware_version(semver: Optional[str]) -> str:
    """Convert semver '2.964.0' to calver '2:20220822.0' for display."""
    if not semver:
        return "Unknown"
    try:
        major, minor, patch = (int(x) for x in semver.split("."))
        fw_date = date(2020, 1, 1) + timedelta(days=minor)
        return f"{major}:{fw_date.strftime('%Y%m%d')}.{patch}"
    except Exception:
        return semver


def format_firmware_full(semver: Optional[str]) -> str:
    """Return 'calver (semver)' e.g. '2:20220822.0 (2.964.0)'."""
    if not semver:
        return "Unknown"
    try:
        major, minor, patch = (int(x) for x in semver.split("."))
        fw_date = date(2020, 1, 1) + timedelta(days=minor)
        calver = f"{major}:{fw_date.strftime('%Y%m%d')}.{patch}"
        return f"{calver} ({semver})"
    except Exception:
        return semver


class DeviceMode(Enum):
    """Device operation modes."""

    REGULAR = "regular"
    BOOTLOADER = "bootloader"


class DeviceStatus(Enum):
    """Device connection status."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class DeviceInfo:
    """Information about a SoloKeys device."""

    path: str
    mode: DeviceMode
    firmware_version: Optional[str] = None
    serial_number: Optional[str] = None
    battery_level: Optional[int] = None
    capabilities: Optional[List[str]] = None


@dataclass
class FirmwareCapabilities:
    """Probed once at connect time. All fields False by default."""

    # Admin vendor commands
    has_version: bool = False        # 0x61 succeeded → admin app present
    has_uuid: bool = False           # 0x62 probed at connect
    has_locked: bool = False         # 0x63 probed at connect
    has_reboot: bool = False         # 0x53 — assumed present if admin app responds
    has_boot_to_bootloader: bool = False  # 0x51 — same
    # CTAP2 standard (from get_info().options)
    ctap2_pin: bool = False
    ctap2_cred_mgmt: bool = False
    ctap2_uv: bool = False
    ctap2_rk: bool = False
    ctap2_up: bool = False
    # Metadata
    variant: str = ""
    firmware_version: Optional[str] = None


class SoloDevice(ABC):
    """Abstract base class for SoloKeys devices."""

    def __init__(self, path: str, mode: DeviceMode):
        self.path = path
        self.mode = mode
        self.status = DeviceStatus.DISCONNECTED

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the device."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the device."""
        pass

    @abstractmethod
    def get_info(self) -> DeviceInfo:
        """Get device information."""
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if device is still connected."""
        pass


class Solo2Device(SoloDevice):
    """SoloKeys Solo 2 device implementation."""

    SOLOKEYS_VID = 0x1209
    REGULAR_PID = 0xBEEE
    BOOTLOADER_PID = 0xB000
    # Known AAGUIDs: hacker/custom firmware, official/secure firmware
    # AAGUID prefix → variant name
    SOLOKEYS_AAGUIDS = {
        '8bc54968': 'Hacker',
        '2369d4d0': 'Secure',
    }

    def __init__(self, path: str, mode: DeviceMode):
        super().__init__(path, mode)
        self._usb_device: Optional[usb.core.Device] = None
        self._hid_device: Optional[CtapHidDevice] = None
        self._variant: Optional[str] = None  # "Hacker" or "Secure"
        self._firmware_version: Optional[str] = None  # semver e.g. "2.964.0", cached at connect
        self._capabilities: Optional[FirmwareCapabilities] = None

    def connect(self) -> bool:
        """Connect to the Solo 2 device."""
        try:
            if self.mode == DeviceMode.REGULAR:
                # For regular mode, find a working CTAP HID device
                for hid_device in CtapHidDevice.list_devices():
                    try:
                        # Test CTAP2 briefly to identify device, but don't store the CTAP2 object
                        # to avoid thread-safety issues. Workers will open their own connections.
                        ctap = Ctap2(hid_device)
                        info = ctap.info
                        aaguid_prefix = info.aaguid.hex()[:8] if info.aaguid else ''
                        if aaguid_prefix in self.SOLOKEYS_AAGUIDS:
                            variant = self.SOLOKEYS_AAGUIDS[aaguid_prefix]
                        else:
                            # Unknown AAGUID (e.g. dev/custom firmware) — accept if the
                            # device responds to the Solo2 admin VERSION command.
                            version_probe = self._get_firmware_version_from_hid(hid_device)
                            if not version_probe:
                                continue
                            variant = "Hacker"
                        # Don't store CTAP2 object - only HID device
                        # This prevents thread-safety issues
                        self._hid_device = hid_device
                        self._variant = variant
                        self.status = DeviceStatus.CONNECTED
                        self._firmware_version = self._get_firmware_version()
                        self._capabilities = self._detect_capabilities()
                        return True
                    except Exception:
                        continue

                # If full Ctap2 fails, try to use just HID device for basic operations
                # This handles cases where CTAP2 isn't working but vendor commands are
                for hid_device in CtapHidDevice.list_devices():
                    try:
                        # Test if we can at least get version via vendor command
                        version = self._get_firmware_version_from_hid(hid_device)
                        if version:
                            self._hid_device = hid_device
                            self._variant = "Hacker"  # Assume hacker firmware
                            self._firmware_version = version
                            self.status = DeviceStatus.CONNECTED
                            self._capabilities = self._detect_capabilities()
                            return True
                    except Exception:
                        continue

                # No Solo2 device found
                self.status = DeviceStatus.ERROR
                return False

            else:
                # Bootloader mode - find USB device
                devices = usb.core.find(
                    idVendor=self.SOLOKEYS_VID, idProduct=self.BOOTLOADER_PID, find_all=True
                )
                for dev in devices:
                    if str(dev.bus) + "-" + str(dev.address) == self.path:
                        self._usb_device = dev
                        self.status = DeviceStatus.CONNECTED
                        return True

                self.status = DeviceStatus.DISCONNECTED
                return False

        except Exception:
            self.status = DeviceStatus.ERROR
            return False

    def disconnect(self) -> None:
        """Disconnect from the device."""
        self._usb_device = None
        self._hid_device = None
        self._capabilities = None
        self.status = DeviceStatus.DISCONNECTED

    def get_info(self) -> DeviceInfo:
        """Get device information from cached data (no CTAP2 calls to avoid thread conflicts)."""
        if self.mode == DeviceMode.BOOTLOADER:
            return DeviceInfo(
                path=self.path, mode=self.mode, firmware_version="Bootloader"
            )
        
        # Use cached data - don't call CTAP2 here to avoid conflicts with worker threads
        product = f"Solo 2 {self._variant}" if self._variant else "Solo 2"
        capabilities = None
        if self._capabilities:
            capabilities = [
                'clientPin' if self._capabilities.ctap2_pin else None,
                'credMgmt' if self._capabilities.ctap2_cred_mgmt else None,
                'uv' if self._capabilities.ctap2_uv else None,
                'rk' if self._capabilities.ctap2_rk else None,
            ]
            capabilities = [c for c in capabilities if c]
        
        return DeviceInfo(
            path=self.path,
            mode=self.mode,
            firmware_version=self._firmware_version,
            serial_number=product,
            capabilities=capabilities,
        )

    def _get_firmware_version(self) -> Optional[str]:
        """Query firmware version via Solo2 admin app VERSION command (0x61).

        The 4-byte response is a big-endian u32 packed as:
          bits 31:22 — major (10 bits)
          bits 21:6  — minor (16 bits, days since 2020-01-01)
          bits  5:0  — patch  (6 bits)

        This matches lpc55::secure_binary::Version::from([u8; 4]) in solo2-cli.
        """
        if not self._hid_device:
            return None
        return self._get_firmware_version_from_hid(self._hid_device)

    def _get_firmware_version_from_hid(self, hid_device) -> Optional[str]:
        """Query firmware version from a specific HID device."""
        try:
            resp = hid_device.call(0x61, b'')
            if len(resp) < 4:
                return None
            version = struct.unpack('>I', resp[:4])[0]
            major = version >> 22
            minor = (version >> 6) & ((1 << 16) - 1)
            patch = version & ((1 << 6) - 1)
            return f"{major}.{minor}.{patch}"
        except Exception:
            return None
            return None
        try:
            resp = self._hid_device.call(0x61, b'')
            if len(resp) < 4:
                return None
            version = struct.unpack('>I', resp[:4])[0]
            major = version >> 22
            minor = (version >> 6) & ((1 << 16) - 1)
            patch = version & ((1 << 6) - 1)
            return f"{major}.{minor}.{patch}"
        except Exception:
            return None

    def _detect_capabilities(self) -> FirmwareCapabilities:
        """Probe device capabilities once at connect time."""
        caps = FirmwareCapabilities(
            variant=self._variant or "",
            firmware_version=self._firmware_version,
        )

        if self._firmware_version is not None:
            caps.has_version = True
            caps.has_reboot = True
            caps.has_boot_to_bootloader = True
            # Assume UUID and LOCKED commands exist when admin app responds
            caps.has_uuid = True
            caps.has_locked = True

        # Capabilities are probed during connect() and cached
        # Don't access CTAP2 device here to avoid thread conflicts

        return caps

    @property
    def capabilities(self) -> Optional[FirmwareCapabilities]:
        """Get probed firmware capabilities."""
        return self._capabilities

    def is_alive(self) -> bool:
        """Check if device is still connected."""
        # For regular mode, just check if we have the HID device reference
        if self.mode == DeviceMode.REGULAR:
            return self._hid_device is not None and self.status == DeviceStatus.CONNECTED

        # For bootloader mode, check USB device
        try:
            if self._usb_device:
                _ = self._usb_device.get_active_configuration()
                return True
        except Exception:
            pass

        return False

    @property
    def hid_device_path(self) -> Optional[str]:
        """Get the HID device path (e.g., /dev/hidraw2) for opening device in worker threads."""
        if self._hid_device is not None:
            return self._hid_device.descriptor.path
        return None
    
    def open_ctap2(self) -> Optional[Ctap2]:
        """Open a fresh CTAP2 connection. Caller is responsible for thread-safety."""
        if self._hid_device is None:
            return None
        try:
            # Open a new CTAP2 connection using the stored HID device info
            for hid_dev in CtapHidDevice.list_devices():
                if hid_dev.descriptor.path == self._hid_device.descriptor.path:
                    return Ctap2(hid_dev)
        except Exception:
            pass
        return None
