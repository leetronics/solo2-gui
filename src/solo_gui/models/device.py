"""Device models for SoloKeys GUI."""

import logging
import struct
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

import usb.core
from fido2.ctap2 import Ctap2

from ..hid_backend import list_ctap_hid_devices


def _get_log_path() -> Path:
    if sys.platform == "win32":
        base = Path.home()
        local_appdata = Path(
            __import__("os").environ.get("LOCALAPPDATA", base / "AppData" / "Local")
        )
        data_dir = local_appdata / "solokeys-gui"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        data_dir = Path.home() / ".local" / "share" / "solokeys-gui"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "solokeys-debug.log"


_log_path = _get_log_path()
logging.basicConfig(
    filename=str(_log_path),
    filemode="a",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
_log = logging.getLogger("solo2device")


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


def firmware_supports_extended_applets(semver: Optional[str]) -> bool:
    """Return True for firmware versions that include Secrets, PIV, and OpenPGP."""
    if not semver:
        return False
    try:
        _major, minor, _patch = (int(x) for x in semver.split("."))
        fw_date = date(2020, 1, 1) + timedelta(days=minor)
        return fw_date >= date(2022, 8, 22)
    except Exception:
        return False


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
    # PIV support - detected from CTAP2 or assumed for Solo 2
    has_piv: bool = False
    # OpenPGP support - same presence assumption as PIV
    has_openpgp: bool = False
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

    def open_hid_device(self):
        """Open a fresh HID handle if the device supports it."""
        return None

    def open_ctap2(self) -> Optional[Ctap2]:
        """Open a fresh CTAP2 connection if the device supports it."""
        return None


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
        self._hid_path: Optional[bytes] = None  # raw bytes path; handle closed after connect()
        self._variant: Optional[str] = None  # "Hacker" or "Secure"
        self._firmware_version: Optional[str] = None  # semver e.g. "2.964.0", cached at connect
        self._capabilities: Optional[FirmwareCapabilities] = None
        self._device_uuid: Optional[str] = None

    def connect(self) -> bool:
        """Connect to the Solo 2 device."""
        try:
            if self.mode == DeviceMode.REGULAR:
                _log.debug("connect() REGULAR path=%s", self.path)
                matched_hid = self.open_hid_device()
                if matched_hid is not None:
                    desc = matched_hid.descriptor
                    version = self._get_firmware_version_from_hid(matched_hid)
                    device_uuid = self._get_uuid_from_hid(matched_hid)
                    variant = "Hacker"
                    ctap_info = None
                    try:
                        ctap = Ctap2(matched_hid)
                        ctap_info = ctap.info
                        aaguid_prefix = ctap_info.aaguid.hex()[:8] if ctap_info.aaguid else ""
                        variant = self.SOLOKEYS_AAGUIDS.get(aaguid_prefix, "Hacker")
                    except Exception as e:
                        _log.debug("connect() CTAP2 GetInfo failed (non-fatal): %s", e)

                    self._hid_path = getattr(desc, "path", None)
                    self._device_uuid = device_uuid
                    if device_uuid:
                        self.path = f"uuid:{device_uuid}"
                    elif self._hid_path is not None:
                        self.path = f"hid:{self._hid_path!r}"
                    self._variant = variant
                    self.status = DeviceStatus.CONNECTED
                    self._firmware_version = version
                    self._capabilities = self._detect_capabilities(ctap_info=ctap_info)
                    _log.debug(
                        "connect() SUCCESS variant=%s fw=%s uuid=%s path=%r",
                        variant,
                        version,
                        device_uuid,
                        self._hid_path,
                    )
                    return True

                _log.debug("connect() FAILED: no matching device found")
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
        self._hid_path = None
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
        """Return the firmware version cached at connect time."""
        return self._firmware_version

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

    def _get_uuid_from_hid(self, hid_device) -> Optional[str]:
        """Query UUID from a specific HID device."""
        try:
            resp = hid_device.call(0x62, b"")
            if len(resp) < 16:
                return None
            uuid_hex = resp[:16].hex()
            return (
                f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-"
                f"{uuid_hex[16:20]}-{uuid_hex[20:32]}"
            )
        except Exception:
            return None

    def _get_firmware_version_pcsc_reader(self, reader) -> Optional[str]:
        """Query firmware version via admin applet over raw pyscard (no FIDO AID selected)."""
        ADMIN_AID = [0xA0, 0x00, 0x00, 0x08, 0x47, 0x00, 0x00, 0x00, 0x01]
        try:
            conn = reader.createConnection()
            conn.connect()
            try:
                select = [0x00, 0xA4, 0x04, 0x00, len(ADMIN_AID)] + ADMIN_AID
                resp, sw1, sw2 = conn.transmit(select)
                if (sw1, sw2) != (0x90, 0x00):
                    return None
                resp, sw1, sw2 = conn.transmit([0x00, 0x61, 0x00, 0x00, 0x00])
                if (sw1, sw2) != (0x90, 0x00) or len(resp) < 4:
                    return None
                version_int = struct.unpack('>I', bytes(resp[:4]))[0]
                major = version_int >> 22
                minor = (version_int >> 6) & ((1 << 16) - 1)
                patch = version_int & ((1 << 6) - 1)
                return f"{major}.{minor}.{patch}"
            finally:
                conn.disconnect()
        except Exception as e:
            _log.debug("_get_firmware_version_pcsc_reader: %s", e)
            return None

    def _detect_capabilities(self, ctap_info=None) -> FirmwareCapabilities:
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

        # Detect PIV support from CTAP2 info or assume true for Solo 2 devices
        # Solo 2 firmware typically includes PIV applet
        if ctap_info is not None:
            # Check CTAP2 extensions for PIV indication
            extensions = getattr(ctap_info, 'extensions', []) or []
            if 'piv' in extensions:
                caps.has_piv = True
            elif self._variant in ('Hacker', 'Secure'):
                # Solo 2 devices with official firmware have PIV
                caps.has_piv = True
        elif self._variant in ('Hacker', 'Secure'):
            # Assume PIV support for Solo 2 devices
            caps.has_piv = True

        # OpenPGP: same presence assumption as PIV
        if ctap_info is not None:
            if self._variant in ('Hacker', 'Secure'):
                caps.has_openpgp = True
        elif self._variant in ('Hacker', 'Secure'):
            caps.has_openpgp = True

        # Capabilities are probed during connect() and cached
        # Don't access CTAP2 device here to avoid thread conflicts

        return caps

    @property
    def capabilities(self) -> Optional[FirmwareCapabilities]:
        """Get probed firmware capabilities."""
        return self._capabilities

    @property
    def device_uuid(self) -> Optional[str]:
        """Stable device UUID if the admin app is available."""
        return self._device_uuid

    def is_alive(self) -> bool:
        """Check if device is still connected."""
        # For regular mode, rely on DeviceMonitor's health checks (no HID probe here)
        if self.mode == DeviceMode.REGULAR:
            return self.status == DeviceStatus.CONNECTED

        # For bootloader mode, check USB device
        try:
            if self._usb_device:
                _ = self._usb_device.get_active_configuration()
                return True
        except Exception:
            pass

        return False

    @property
    def hid_device_path(self) -> Optional[bytes]:
        """Get the device path stored at connect time."""
        return self._hid_path

    def _matches_hid_device(self, hid_device) -> bool:
        """Return True if the HID device matches this Solo 2 device."""
        desc = getattr(hid_device, "descriptor", None)
        if not desc:
            return False
        desc_path = getattr(desc, "path", None)

        if self._hid_path is not None and desc_path == self._hid_path:
            return True

        if self._device_uuid:
            return self._get_uuid_from_hid(hid_device) == self._device_uuid

        if self.path.startswith("uuid:"):
            return self._get_uuid_from_hid(hid_device) == self.path.split(":", 1)[1]

        if self.path.startswith("hid:"):
            return self.path == f"hid:{desc_path!r}"

        return False

    def open_hid_device(self):
        """Open a fresh HID device handle for this device."""
        fallback = None
        allow_fallback = self._device_uuid is None and not self.path.startswith("uuid:")
        for hid_device in list_ctap_hid_devices():
            desc = getattr(hid_device, "descriptor", None)
            if not desc:
                continue
            if allow_fallback and fallback is None:
                version = self._get_firmware_version_from_hid(hid_device)
                if version:
                    fallback = hid_device
            if self._matches_hid_device(hid_device):
                self._hid_path = getattr(desc, "path", None)
                return hid_device
        if allow_fallback and fallback is not None:
            desc = getattr(fallback, "descriptor", None)
            self._hid_path = getattr(desc, "path", None)
            if self._device_uuid is None:
                self._device_uuid = self._get_uuid_from_hid(fallback)
            return fallback
        return None

    def open_ctap2(self) -> Optional[Ctap2]:
        """Open a fresh CTAP2 connection. Caller is responsible for thread-safety."""
        try:
            hid_device = self.open_hid_device()
            if hid_device is None:
                return None
            return Ctap2(hid_device)
        except Exception:
            return None
