"""TOTP/Secrets worker thread for SoloKeys GUI using DeviceManager.

This module implements TOTP/HOTP/Secrets functionality using DeviceManager
for thread-safe device access.

Protocol: OATH compatible (Yubikey-style) over CTAPHID vendor command 0x70.
"""

from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
import base64
import hmac
import hashlib
import struct
import time

from PySide6.QtCore import QObject, Signal

from solo_gui.device_manager import DeviceManager


class OtpKind(Enum):
    """OTP credential types."""
    TOTP = auto()
    HOTP = auto()

    def __str__(self) -> str:
        return self.name


class OtherKind(Enum):
    """Other credential types supported by secrets app."""
    HMAC = auto()
    REVERSE_HOTP = auto()

    def __str__(self) -> str:
        return self.name


CredentialKind = Union[OtpKind, OtherKind]


class Algorithm(Enum):
    """Hash algorithms for OTP generation."""
    SHA1 = 0
    SHA256 = 1
    SHA512 = 2


@dataclass
class Credential:
    """Credential information for secrets/TOTP app."""
    id: bytes
    otp: Optional[OtpKind] = None
    other: Optional[OtherKind] = None
    algorithm: Algorithm = Algorithm.SHA1
    digits: int = 6
    period: int = 30
    login: Optional[bytes] = None
    password: Optional[bytes] = None
    metadata: Optional[bytes] = None
    touch_required: bool = False
    protected: bool = False
    encrypted: bool = False

    @property
    def name(self) -> str:
        return self.id.decode('utf-8', errors='replace')

    @property
    def is_otp(self) -> bool:
        return self.otp is not None


@dataclass
class OtpResult:
    """Result of OTP generation."""
    credential: Credential
    code: str
    counter: Optional[int] = None


@dataclass
class SecretsAppStatus:
    """Status information from secrets app."""
    supported: bool
    version: str
    pin_set: bool
    pin_attempts_remaining: Optional[int]
    credentials_count: int
    max_credentials: int


class SecretsAppProtocol:
    """OATH protocol for secrets app communication via CTAPHID."""
    
    VENDOR_CMD = 0x70
    
    # OATH instruction codes (ISO 7816 INS field)
    INS_PUT = 0x01           # Register/Add credential
    INS_DELETE = 0x02        # Delete credential
    INS_SET_CODE = 0x03      # Set PIN/password
    INS_RESET = 0x04         # Reset app
    INS_LIST = 0xa1          # List credentials
    INS_CALCULATE = 0xa2     # Calculate OTP
    INS_VALIDATE = 0xa3      # Validate PIN
    INS_CALCULATE_ALL = 0xa4 # Calculate all TOTPs
    INS_SEND_REMAINING = 0xa5
    INS_VERIFY_CODE = 0xb1   # Reverse HOTP verify
    INS_VERIFY_PIN = 0xb2    # Verify PIN
    INS_CHANGE_PIN = 0xb3    # Change PIN
    INS_SET_PIN = 0xb4       # Set initial PIN
    INS_GET_CREDENTIAL = 0xb5
    INS_UPDATE_CREDENTIAL = 0xb7
    
    # ISO 7816 status words
    SW_SUCCESS = (0x90, 0x00)
    SW_NOT_FOUND = (0x6a, 0x82)
    SW_WRONG_PIN = (0x63, 0x00)
    SW_PIN_REQUIRED = (0x69, 0x82)
    SW_TOUCH_REQUIRED = (0x69, 0x85)
    SW_INVALID_DATA = (0x6a, 0x80)
    
    @classmethod
    def parse_status(cls, response: bytes) -> Tuple[int, int, bytes]:
        """Parse ISO 7816 response: returns (sw1, sw2, data).
        
        Note: secrets-app returns SW at the START of response (non-standard),
        not at the end like typical ISO 7816.
        """
        if len(response) < 2:
            return (0x6f, 0x00, b"")  # Unknown error
        
        # Check if response starts with valid SW (9000, 61xx, 6axx, etc.)
        potential_sw1 = response[0]
        if potential_sw1 in (0x90, 0x61, 0x6a, 0x69, 0x63):
            # SW is at the start
            sw1, sw2 = response[0], response[1]
            data = response[2:] if len(response) > 2 else b""
        else:
            # Fallback: SW at the end (standard ISO 7816)
            sw1, sw2 = response[-2], response[-1]
            data = response[:-2] if len(response) > 2 else b""
        
        return (sw1, sw2, data)


class TotpWorker(QObject):
    """Worker thread for TOTP/Secrets operations using DeviceManager."""

    status_checked = Signal(object)
    credentials_loaded = Signal(list)
    credential_added = Signal(bool, str)
    credential_updated = Signal(bool, str)
    credential_deleted = Signal(bool, str)
    credential_data_loaded = Signal(object)
    otp_generated = Signal(object)
    pin_verified = Signal(bool, str)
    pin_changed = Signal(bool, str)
    pin_required = Signal()
    touch_required = Signal()
    error_occurred = Signal(str)

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._device_manager = DeviceManager.get_instance()
        self._pin: Optional[str] = None
        self._pin_verified: bool = False
        self._pin_is_set: bool = False  # Track if device has PIN configured

    @property
    def pin_is_set(self) -> bool:
        """Return True if device has a PIN set."""
        return self._pin_is_set

    @property
    def pin_is_verified(self) -> bool:
        """Return True if PIN has been verified/unlocked."""
        return self._pin_verified

    def set_pin(self, pin: str) -> None:
        """Set PIN for authenticated operations."""
        self._pin = pin
        self._pin_verified = False

    def clear_pin(self) -> None:
        """Clear cached PIN."""
        self._pin = None
        self._pin_verified = False

    def _send_apdu(self, ins: int, p1: int = 0x00, p2: int = 0x00, 
                   data: bytes = b"", le: int = 0x00,
                   callback=None) -> None:
        """Send ISO 7816 APDU to secrets app via DeviceManager.
        
        APDU format: CLA | INS | P1 | P2 | [Lc | Data] | Le
        """
        # Build APDU
        apdu = bytearray([0x00, ins, p1, p2])  # CLA=00 (ISO 7816 class)
        
        if data:
            apdu.append(len(data))  # Lc
            apdu.extend(data)       # Data field
        
        apdu.append(le)  # Le (expected response length, 0=max)
        
        def on_response(response, error):
            if error:
                if callback:
                    callback(0x6f, 0x00, str(error).encode())  # Unknown error
                return
            
            if not response or len(response) < 2:
                if callback:
                    callback(0x6f, 0x00, b"No response")
                return
            
            sw1, sw2, data_out = SecretsAppProtocol.parse_status(response)
            
            if callback:
                callback(sw1, sw2, data_out)
        
        self._device_manager.vendor_command(
            SecretsAppProtocol.VENDOR_CMD,
            bytes(apdu),
            on_response,
            operation_id=f"totp_{ins:02x}"
        )

    def check_status(self) -> None:
        """Check if secrets app is supported and get status.
        
        Uses SELECT command to detect PIN status without requiring touch.
        SELECT response contains Tag 0x82 (PINCounter) only if PIN is set.
        Then uses LIST to get credential count.
        """
        # Store PIN info from SELECT to use after LIST
        pin_info = {'set': False, 'attempts': None}
        
        def on_list(sw1, sw2, response, pin_set, pin_attempts):
            """Handle LIST response with PIN info from SELECT."""
            cred_count = 0
            if sw1 == 0x90 and sw2 == 0x00:
                # Count credentials from TLV response
                offset = 0
                while offset < len(response):
                    if response[offset] == 0x72:
                        cred_count += 1
                        if offset + 1 < len(response):
                            entry_len = response[offset + 1]
                            offset += 2 + entry_len
                        else:
                            break
                    else:
                        break
            
            status = SecretsAppStatus(
                supported=True,
                version="1.0.0",
                pin_set=pin_set,
                pin_attempts_remaining=pin_attempts,
                credentials_count=cred_count,
                max_credentials=50,
            )
            self.status_checked.emit(status)
        
        def do_list():
            """Call LIST command with PIN info from SELECT."""
            def on_list_response(sw1, sw2, response):
                on_list(sw1, sw2, response, pin_info['set'], pin_info['attempts'])
            self._send_apdu(SecretsAppProtocol.INS_LIST, callback=on_list_response)
        
        def on_select(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                # Parse SELECT response to detect PIN status
                # Format: 79 03 <ver> 71 08 <salt> [82 01 <attempts>] 8F 04 <serial>
                # Tag 0x82 (PINCounter) present only if PIN is set
                offset = 0
                while offset < len(response):
                    if offset >= len(response):
                        break
                    
                    tag = response[offset]
                    offset += 1
                    
                    if offset >= len(response):
                        break
                    
                    length = response[offset]
                    offset += 1
                    
                    if offset + length > len(response):
                        break
                    
                    if tag == 0x82 and length >= 1:  # PINCounter
                        pin_info['set'] = True
                        pin_info['attempts'] = response[offset]
                    
                    offset += length
                
                # Update worker state
                self._pin_is_set = pin_info['set']
                
                # Now get credential count via LIST
                do_list()
            elif sw1 == 0x6A and sw2 == 0x82:
                # Application not found
                status = SecretsAppStatus(
                    supported=False,
                    version="0.0.0",
                    pin_set=False,
                    pin_attempts_remaining=None,
                    credentials_count=0,
                    max_credentials=50,
                )
                self.status_checked.emit(status)
            else:
                # Other error - try LIST as fallback
                self._check_status_with_list()
        
        # Use SELECT (0xA4) with OATH AID
        # AID for Yubico OATH: A0 00 00 05 27 21 01
        oath_aid = bytes([0xA0, 0x00, 0x00, 0x05, 0x27, 0x21, 0x01])
        # SELECT by name: CLA=00, INS=A4, P1=04, P2=00, Lc=len(AID), AID
        select_apdu = bytes([0x00, 0xA4, 0x04, 0x00, len(oath_aid)]) + oath_aid
        
        self._device_manager.vendor_command(
            SecretsAppProtocol.VENDOR_CMD,
            select_apdu,
            lambda response, error: on_select(
                response[0] if response and len(response) > 0 else 0x6F,
                response[1] if response and len(response) > 1 else 0x00,
                response[2:] if response and len(response) > 2 else b""
            ) if not error else on_select(0x6F, 0x00, b""),
            operation_id="totp_select"
        )
    
    def _check_status_with_list(self) -> None:
        """Fallback: Check status using LIST command."""
        def on_list(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                cred_count = 0
                offset = 0
                while offset < len(response):
                    if response[offset] == 0x72:
                        cred_count += 1
                        if offset + 1 < len(response):
                            entry_len = response[offset + 1]
                            offset += 2 + entry_len
                        else:
                            break
                    else:
                        break
                
                status = SecretsAppStatus(
                    supported=True,
                    version="1.0.0",
                    pin_set=None,  # Unknown with LIST
                    pin_attempts_remaining=None,
                    credentials_count=cred_count,
                    max_credentials=50,
                )
                self.status_checked.emit(status)
            elif (sw1, sw2) == SecretsAppProtocol.SW_PIN_REQUIRED:
                status = SecretsAppStatus(
                    supported=True,
                    version="1.0.0",
                    pin_set=True,
                    pin_attempts_remaining=None,
                    credentials_count=0,
                    max_credentials=50,
                )
                self.status_checked.emit(status)
            else:
                status = SecretsAppStatus(
                    supported=False,
                    version="0.0.0",
                    pin_set=False,
                    pin_attempts_remaining=None,
                    credentials_count=0,
                    max_credentials=50,
                )
                self.status_checked.emit(status)
        
        self._send_apdu(SecretsAppProtocol.INS_LIST, callback=on_list)

    def load_credentials(self) -> None:
        """Load all credentials from the device."""
        def on_list(sw1, sw2, response):
            credentials: List[Credential] = []
            
            if (sw1, sw2) == SecretsAppProtocol.SW_PIN_REQUIRED:
                self.pin_required.emit()
                return
            
            if sw1 != 0x90 or sw2 != 0x00:
                self.error_occurred.emit(f"Failed to list credentials: {sw1:02x}{sw2:02x}")
                return
            
            if len(response) == 0:
                self.credentials_loaded.emit(credentials)
                return
            
            # Parse TLV-encoded response
            # Version 1 format: 72 <len> <kind+algo> <label...> <properties>
            #   entry_len = label.len() + 2 (1 for kind_algo + 1 for properties)
            # Version 0 format: 72 <len> <kind+algo> <label...>
            #   entry_len = label.len() + 1 (1 for kind_algo)
            # Properties byte: bit 0 = touch_required, bit 1 = encrypted (PIN protected)
            offset = 0
            print(f"[TOTP] LIST response ({len(response)} bytes): {response.hex()}")
            
            while offset < len(response):
                # Check for Tag::NameList (0x72)
                if offset >= len(response) or response[offset] != 0x72:
                    break
                offset += 1
                
                if offset >= len(response):
                    break
                
                entry_len = response[offset]
                offset += 1
                
                if offset + entry_len > len(response):
                    break
                
                # Parse entry
                # First byte: combined kind + algorithm
                if entry_len < 1:
                    break
                
                kind_algo = response[offset]
                offset += 1
                
                # Determine type from kind (high nibble)
                kind = kind_algo & 0xF0
                if kind == 0x20:
                    otp_kind = OtpKind.TOTP
                elif kind == 0x10:
                    otp_kind = OtpKind.HOTP
                else:
                    otp_kind = None
                
                # Remaining bytes are: <label...> [<properties>]
                # In version 1: entry_len = label_len + 2 (kind_algo + properties)
                # In version 0: entry_len = label_len + 1 (kind_algo only)
                remaining_len = entry_len - 1  # Subtract kind_algo byte
                
                if remaining_len > 0:
                    entry_data = response[offset:offset + remaining_len]
                    offset += remaining_len
                    
                    # Check if we have a properties byte
                    # Version 1 has properties, version 0 doesn't
                    # We can't know the version, but we can infer:
                    # If remaining_len > 0, the last byte is properties
                    # and the rest is the label
                    touch_required = False
                    protected = False
                    
                    if remaining_len >= 1:
                        # Last byte is properties (only in version 1 format)
                        properties = entry_data[-1]
                        label_bytes = entry_data[:-1]
                        
                        # Properties bits in LIST response (different from Tag::Property values):
                        # bit 0 (0x01) = touch_required
                        # bit 1 (0x02) = encrypted (PIN protected)
                        # bit 2 (0x04) = pws_data_exist
                        touch_required = bool(properties & 0x01)
                        protected = bool(properties & 0x02)
                        print(f"[TOTP] Parsed credential: label={label_bytes.decode('utf-8', errors='replace')}, properties=0x{properties:02x}, touch={touch_required}, protected={protected}")
                    else:
                        label_bytes = entry_data
                        print(f"[TOTP] Parsed credential (no props): label={label_bytes.decode('utf-8', errors='replace')}")
                    
                    cred = Credential(
                        id=label_bytes,
                        otp=otp_kind,
                        touch_required=touch_required,
                        protected=protected,
                    )
                    credentials.append(cred)
            
            self.credentials_loaded.emit(credentials)
        
        # Request version 1 format (includes properties byte)
        # Version is sent in data field, not P1
        self._send_apdu(SecretsAppProtocol.INS_LIST, data=bytes([0x01]), callback=on_list)

    def add_credential(self, credential: Credential, secret: bytes) -> None:
        """Add a new credential to the device using TLV format."""
        def on_add(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                self.credential_added.emit(True, "")
            elif (sw1, sw2) == SecretsAppProtocol.SW_PIN_REQUIRED:
                self.credential_added.emit(False, "PIN required")
                self.pin_required.emit()
            elif sw1 == 0x69 and sw2 == 0x82:
                self.credential_added.emit(False, "PIN not set. Please set a PIN first to create PIN-protected credentials.")
            else:
                self.credential_added.emit(False, f"Failed to add credential: {sw1:02x}{sw2:02x}")

        def do_add():
            # Build TLV-encoded PUT data
            payload = bytearray()

            # Tag::Name (0x71)
            name_bytes = credential.id
            payload.append(0x71)
            payload.append(len(name_bytes))
            payload.extend(name_bytes)

            # Tag::Key (0x73): kind+algo byte, digits, secret
            if credential.otp == OtpKind.TOTP:
                kind_algo = 0x21  # TOTP (0x20) + SHA1 (0x01)
            elif credential.otp == OtpKind.HOTP:
                kind_algo = 0x11  # HOTP (0x10) + SHA1 (0x01)
            else:
                kind_algo = 0x21
            digits = credential.digits if hasattr(credential, 'digits') else 6
            key_data = bytearray([kind_algo, digits]) + bytearray(secret)
            payload.append(0x73)
            payload.append(len(key_data))
            payload.extend(key_data)

            # Tag::Property (0x78) — compact 2-byte encoding [tag, value], no length byte.
            # The firmware's Properties decoder reads exactly [tag, value].
            # Bits: 0x02 = RequireTouch, 0x04 = PINEncrypt.
            if credential.touch_required or credential.protected:
                properties = 0x00
                if credential.touch_required:
                    properties |= 0x02
                if credential.protected:
                    properties |= 0x04
                payload.append(0x78)
                payload.append(properties)

            # Tag::InitialMovingFactor (0x7A) for HOTP
            if credential.otp == OtpKind.HOTP:
                payload.append(0x7A)
                payload.append(0x04)
                payload.extend([0x00, 0x00, 0x00, 0x00])

            self._send_apdu(SecretsAppProtocol.INS_PUT, data=bytes(payload), callback=on_add)

        # The firmware uses per-request authorization: the PIN session is cleared after
        # every non-VerifyPin command. Re-verify the cached PIN immediately before adding
        # a PIN-protected credential so the session is fresh when the PUT is sent.
        if credential.protected and self._pin:
            def on_reverify(sw1, sw2, response):
                if sw1 == 0x90 and sw2 == 0x00:
                    do_add()
                else:
                    tries = sw2 & 0x0F if sw1 == 0x63 else None
                    msg = f"PIN verification failed ({tries} tries remaining)" if tries is not None else f"PIN verification failed: {sw1:02x}{sw2:02x}"
                    self.credential_added.emit(False, msg)
            pin_bytes = self._pin.encode()
            self._send_apdu(SecretsAppProtocol.INS_VERIFY_PIN,
                            data=bytes([0x80, len(pin_bytes)]) + pin_bytes,
                            callback=on_reverify)
        else:
            do_add()

    def delete_credential(self, credential: Credential) -> None:
        """Delete a credential from the device."""
        def on_delete(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                self.credential_deleted.emit(True, "")
            else:
                self.credential_deleted.emit(False, f"Failed to delete: {sw1:02x}{sw2:02x}")
        
        # Build DELETE data in TLV format: 71 <len> <name>
        # Tag::Name (0x71)
        payload = bytearray()
        payload.append(0x71)  # Tag::Name
        payload.append(len(credential.id))
        payload.extend(credential.id)
        
        self._send_apdu(SecretsAppProtocol.INS_DELETE, data=bytes(payload), callback=on_delete)

    def generate_otp(self, credential: Credential, touch_confirmed: bool = False) -> None:
        """Generate OTP code for a credential."""
        def on_calculate(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                # Parse TLV response: 76 05 <digits> <4-byte-code>
                # Tag::Response (0x76), length 5, digits, 4-byte truncated digest
                if len(response) >= 7 and response[0] == 0x76:
                    # TLV format: 76 <len> <digits> <4-byte-code>
                    # response[0] = 0x76 (Tag::Response)
                    # response[1] = length (should be 5)
                    # response[2] = digits (e.g., 6 or 8)
                    # response[3:7] = 4-byte truncated digest
                    tlv_len = response[1]
                    digits = response[2]
                    code_bytes = response[3:7]
                    # Convert 4-byte big-endian value to OTP code
                    code_value = int.from_bytes(code_bytes, 'big')
                    # Truncate to required digits (modulo 10^digits)
                    code_value = code_value % (10 ** digits)
                    code = str(code_value).zfill(digits)
                    result = OtpResult(credential=credential, code=code)
                    self.otp_generated.emit(result)
                elif len(response) >= 5:
                    # Raw format: <digits> <4-byte-code>
                    digits = response[0]
                    code_bytes = response[1:5]
                    code_value = int.from_bytes(code_bytes, 'big')
                    code_value = code_value % (10 ** digits)
                    code = str(code_value).zfill(digits)
                    result = OtpResult(credential=credential, code=code)
                    self.otp_generated.emit(result)
                else:
                    self.error_occurred.emit(f"Invalid OTP response format: {response.hex()}")
            elif (sw1, sw2) == SecretsAppProtocol.SW_TOUCH_REQUIRED:
                self.touch_required.emit()
            else:
                self.error_occurred.emit(f"Failed to generate OTP: {sw1:02x}{sw2:02x}")
        
        # Build CALCULATE data in TLV format
        # Format: 71 <len> <name> 74 <len> <challenge>
        # Tag::Name (0x71), Tag::Challenge (0x74)
        payload = bytearray()
        
        # Tag::Name (0x71)
        payload.append(0x71)  # Tag::Name
        payload.append(len(credential.id))
        payload.extend(credential.id)
        
        # Tag::Challenge (0x74)
        # Challenge: timestamp for TOTP (8 bytes, big-endian)
        if credential.otp == OtpKind.TOTP:
            challenge = struct.pack('>Q', int(time.time()) // credential.period)
        else:
            challenge = struct.pack('>Q', 0)  # Counter for HOTP
        
        payload.append(0x74)  # Tag::Challenge
        payload.append(len(challenge))
        payload.extend(challenge)
        
        self._send_apdu(SecretsAppProtocol.INS_CALCULATE, p1=0x00, p2=0x01,
                       data=bytes(payload), callback=on_calculate)

    def verify_pin(self, pin: str) -> None:
        """Verify PIN for the secrets app."""
        def on_verify(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                self._pin = pin
                self._pin_verified = True
                self.pin_verified.emit(True, "")
            elif sw1 == 0x63:
                tries = sw2 & 0x0F
                self.pin_verified.emit(False, f"Wrong PIN ({tries} tries remaining)")
            elif sw1 == 0x69 and sw2 == 0x83:
                self.pin_verified.emit(False, "PIN blocked")
            elif sw1 == 0x69 and sw2 == 0x82:
                self.pin_verified.emit(False, "PIN is not set")
            else:
                self.pin_verified.emit(False, f"PIN verification failed: {sw1:02x}{sw2:02x}")
        
        # Use VERIFY_PIN (0xb2) with TLV format
        # Tag::Password (0x80) + length + PIN
        pin_bytes = pin.encode()
        payload = bytes([0x80, len(pin_bytes)]) + pin_bytes
        self._send_apdu(SecretsAppProtocol.INS_VERIFY_PIN, data=payload, callback=on_verify)

    def set_new_pin(self, pin: str) -> None:
        """Set a new PIN for the secrets app."""
        def on_set(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                self._pin = pin
                self._pin_verified = True
                self.pin_changed.emit(True, "")
            elif sw1 == 0x69 and sw2 == 0x82:
                # Security status not satisfied = PIN already set
                self.pin_changed.emit(False, "PIN is already set. Use 'Change PIN' instead.")
            else:
                self.pin_changed.emit(False, f"Failed to set PIN: {sw1:02x}{sw2:02x}")
        
        # Use TLV format for SET_PIN (0xb4)
        # Tag::Password (0x80) + length + PIN
        pin_bytes = pin.encode()
        payload = bytes([0x80, len(pin_bytes)]) + pin_bytes
        self._send_apdu(SecretsAppProtocol.INS_SET_PIN, data=payload, callback=on_set)

    def change_pin(self, old_pin: str, new_pin: str) -> None:
        """Change the secrets app PIN."""
        def on_change(sw1, sw2, response):
            if sw1 == 0x90 and sw2 == 0x00:
                self._pin = new_pin
                self.pin_changed.emit(True, "")
            elif (sw1, sw2) == SecretsAppProtocol.SW_WRONG_PIN:
                self.pin_changed.emit(False, "Current PIN is incorrect")
            else:
                self.pin_changed.emit(False, f"Failed to change PIN: {sw1:02x}{sw2:02x}")
        
        # Use TLV format for CHANGE_PIN (0xb3)
        # Tag::Password (0x80) for current PIN, Tag::NewPassword (0x81) for new PIN
        old_pin_bytes = old_pin.encode()
        new_pin_bytes = new_pin.encode()
        payload = bytes([0x80, len(old_pin_bytes)]) + old_pin_bytes
        payload += bytes([0x81, len(new_pin_bytes)]) + new_pin_bytes
        self._send_apdu(SecretsAppProtocol.INS_CHANGE_PIN, data=payload, callback=on_change)


class FirmwareExtensionSpec:
    """Documentation for secrets app firmware extension."""

    @staticmethod
    def get_integration_plan() -> str:
        return """
Firmware Extension: TOTP/Secrets Application
==============================================

OATH-compatible secrets app for Solo2 firmware.
Uses CTAPHID vendor command 0x70 with ISO 7816 APDU format.

Supported Commands:
- LIST (0xa1): List all credentials
- PUT (0x01): Add/register credential  
- DELETE (0x02): Delete credential
- CALCULATE (0xa2): Generate OTP code
- VALIDATE (0xa3): Verify PIN
- SET_PIN (0xb4): Set initial PIN
- CHANGE_PIN (0xb3): Change PIN

Build: cargo build --features oath
        """
