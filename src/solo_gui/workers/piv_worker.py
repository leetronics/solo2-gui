"""PIV worker thread for SoloKeys GUI.

PIV (Personal Identity Verification) functionality uses the CCID/smartcard
interface, which requires PCSC support. This worker attempts to use pyscard
for PCSC communication, but gracefully handles the case when it's not available.

To enable full PIV functionality:
1. Install PCSC daemon: sudo apt install pcscd pcsc-tools
2. Install pyscard: pip install pyscard
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import struct
import time
import platform

DEFAULT_MANAGEMENT_KEY = "010203040506070801020304050607080102030405060708"

from PySide6.QtCore import QObject, Signal

from solo2.pcsc import (
    PCSC_AVAILABLE,
    PCSC_IMPORT_ERROR,
    iter_pcsc_connections,
    list_pcsc_reader_names,
)


class PivKeyType(Enum):
    """PIV key types."""

    RSA_2048 = "rsa2048"
    ECC_P256 = "eccp256"
    ECC_P384 = "eccp384"




class PivSlot(Enum):
    """PIV key slots.

    Standard PIV slots as defined in NIST SP 800-73-4.
    """

    AUTHENTICATION = 0x9A  # PIV Authentication
    SIGNATURE = 0x9C  # Digital Signature
    KEY_MANAGEMENT = 0x9D  # Key Management
    CARD_AUTH = 0x9E  # Card Authentication


# PIV Application AID
PIV_AID = [0xA0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00, 0x01, 0x00]

# PIV Instructions
INS_SELECT = 0xA4
INS_GET_DATA = 0xCB
INS_VERIFY = 0x20
INS_CHANGE_REFERENCE_DATA = 0x24
INS_RESET_RETRY_COUNTER = 0x2C
INS_GENERATE_ASYMMETRIC = 0x47
INS_PUT_DATA = 0xDB
INS_AUTHENTICATE = 0x87
INS_RESET_PIV = 0xFB

# PIV Data Object IDs (tags)
TAG_CERTIFICATE = {
    PivSlot.AUTHENTICATION: [0x5F, 0xC1, 0x05],
    PivSlot.SIGNATURE: [0x5F, 0xC1, 0x0A],
    PivSlot.KEY_MANAGEMENT: [0x5F, 0xC1, 0x0B],
    PivSlot.CARD_AUTH: [0x5F, 0xC1, 0x01],
}

# Key reference for slots
KEY_REFERENCE = {
    PivSlot.AUTHENTICATION: 0x9A,
    PivSlot.SIGNATURE: 0x9C,
    PivSlot.KEY_MANAGEMENT: 0x9D,
    PivSlot.CARD_AUTH: 0x9E,
}

# Algorithm IDs for key generation
ALGORITHM_ID = {
    PivKeyType.RSA_2048: 0x07,
    PivKeyType.ECC_P256: 0x11,
    PivKeyType.ECC_P384: 0x14,
}



@dataclass
class PivKey:
    """PIV key information."""

    slot: PivSlot
    key_type: Optional[PivKeyType]
    algorithm: str
    public_key_pem: Optional[str] = None
    has_certificate: bool = False


@dataclass
class PivCertificate:
    """PIV certificate information."""

    slot: PivSlot
    subject: str
    issuer: str
    serial: str
    not_before: str
    not_after: str
    certificate_der: bytes


_KEY_TYPE_LABELS = {
    PivKeyType.ECC_P256: "ECC P-256",
    PivKeyType.ECC_P384: "ECC P-384",
    PivKeyType.RSA_2048: "RSA 2048",
}


@dataclass
class SlotInfo:
    """PIV slot state — combined key + certificate info."""

    slot: PivSlot
    has_key: bool
    key_type_str: Optional[str]
    certificate: Optional[PivCertificate]


class PivWorker(QObject):
    """Worker thread for PIV operations.

    Uses PCSC/smartcard interface for PIV operations. Requires pyscard library
    and PCSC daemon to be installed on the system.
    """

    piv_probed = Signal(bool)   # emitted once at connect time: True = PIV applet found
    keys_loaded = Signal(list)  # list[PivKey]
    certificates_loaded = Signal(list)  # list[PivCertificate]
    slots_loaded = Signal(list)  # list[SlotInfo], always 4 entries
    key_generated = Signal(bool, str, bytes, object)  # success, error, pubkey DER, slot (or None)
    key_deleted = Signal(bool, str)  # success, error message
    certificate_imported = Signal(bool, str)  # success, error message
    certificate_exported = Signal(bool, str, bytes)  # success, error/path, cert data
    pin_changed = Signal(bool, str)  # success, error message
    pin_status_updated = Signal(dict)  # status info
    key_probe_completed = Signal(bool, str)  # success, status message
    reset_completed = Signal(bool, str)  # success, message
    pcsc_status = Signal(bool, str)  # available, message
    error_occurred = Signal(str)  # error message
    diagnose_result = Signal(str)  # diagnostic report

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._connection = None
        self._selected = False
        self._key_cache = {}  # Session-local key hints after generate/import operations

    def get_key_cache(self) -> Dict[PivSlot, dict]:
        """Return a shallow copy of the session-local key cache."""
        return {slot: dict(info) for slot, info in self._key_cache.items()}

    def set_key_cache(self, cache: Dict[PivSlot, dict]) -> None:
        """Restore a previously captured session-local key cache."""
        self._key_cache = {slot: dict(info) for slot, info in (cache or {}).items()}

    def check_pcsc_available(self) -> bool:
        """Check if PCSC is available and emit status."""
        if not PCSC_AVAILABLE:
            if platform.system() == "Windows":
                message = (
                    "PCSC support is not available in the app.\n"
                    "The Windows Smart Card service may still be running.\n\n"
                    "Likely causes:\n"
                    "  - pyscard is missing from the build\n"
                    "  - a pyscard native module failed to load\n"
                    "  - the PC/SC runtime is not accessible\n"
                )
                if PCSC_IMPORT_ERROR:
                    message += f"\nImport error: {PCSC_IMPORT_ERROR}"
            else:
                message = (
                    "PCSC not available. Install pyscard and PCSC daemon:\n"
                    "  sudo apt install pcscd pcsc-tools\n"
                    "  pip install pyscard"
                )
            self.pcsc_status.emit(
                False,
                message,
            )
            return False
        return True

    def _connect(self) -> bool:
        """Connect to the PIV applet on the device.

        Tries all available PCSC readers and uses the first one that
        responds successfully to PIV AID SELECT.
        """
        if not self.check_pcsc_available():
            return False

        # SELECT variants to try.  Short 5-byte AID (like ykman uses), 9-byte AID
        # (without version bytes), and full 11-byte AID — each in Case-3 (no Le)
        # and Case-4 (Le=0x00) forms.
        SHORT_AID = [0xA0, 0x00, 0x00, 0x03, 0x08]
        # 9-byte AID is the minimum accepted by piv-authenticator (new_truncatable min=9).
        # solo2-cli sends exactly the 9-byte AID + Le=0x00 (Case 4).
        MID_AID = [0xA0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00]
        select_variants = [
            [0x00, INS_SELECT, 0x04, 0x00, len(MID_AID)] + MID_AID + [0x00],     # 9-byte, Case 4  ← solo2-cli format
            [0x00, INS_SELECT, 0x04, 0x00, len(MID_AID)] + MID_AID,              # 9-byte, Case 3
            [0x00, INS_SELECT, 0x04, 0x00, len(PIV_AID)] + PIV_AID + [0x00],     # full, Case 4
            [0x00, INS_SELECT, 0x04, 0x00, len(PIV_AID)] + PIV_AID,              # full, Case 3
            [0x00, INS_SELECT, 0x04, 0x00, len(SHORT_AID)] + SHORT_AID + [0x00], # short, Case 4
            [0x00, INS_SELECT, 0x04, 0x00, len(SHORT_AID)] + SHORT_AID,          # short, Case 3
        ]

        last_error = "No PCSC readers found"
        found_any_reader = False

        for connection in iter_pcsc_connections():
            found_any_reader = True
            try:
                # --- Try SELECT AID ---
                selected = False
                for select_cmd in select_variants:
                    response, sw1, sw2 = connection.transmit(select_cmd)
                    # SW=9000 or SW=61xx (success + FCI pending) both mean OK
                    if (sw1 == 0x90 and sw2 == 0x00) or sw1 == 0x61:
                        selected = True
                        break
                    last_error = (
                        f"PIV SELECT failed on '{connection.reader_name}': "
                        f"SW={sw1:02X}{sw2:02X}"
                    )

                if selected:
                    self._connection = connection
                    self._selected = True
                    return True

                # --- SELECT failed: probe for pre-selected PIV.
                # VERIFY (INS=0x20, P2=0x80) returns 63Cx/69xx on a PIV card;
                # non-PIV interfaces return 6D00 (INS not supported).
                probe_cmd = [0x00, INS_VERIFY, 0x00, 0x80]
                response, sw1, sw2 = connection.transmit(probe_cmd)
                piv_sw = (
                    sw1 == 0x63
                    or (sw1 == 0x69 and sw2 in (0x82, 0x83))
                    or (sw1 == 0x90 and sw2 == 0x00)
                )
                if piv_sw:
                    self._connection = connection
                    self._selected = True
                    return True

                select_sw = last_error.split("SW=")[-1] if "SW=" in last_error else "?"
                last_error = (
                    f"PIV not accessible on '{connection.reader_name}' "
                    f"(SELECT SW={select_sw}, probe SW={sw1:02X}{sw2:02X})"
                )
                connection.close()

            except Exception as e:
                last_error = f"Error on '{connection.reader_name}': {e}"
                connection.close()

        if not found_any_reader:
            if platform.system() == "Windows":
                self.error_occurred.emit(
                    "No PCSC readers found.\n"
                    "The Smart Card service may be running, but Windows is not exposing a CCID reader for the device.\n\n"
                    "Check:\n"
                    "  - the SoloKeys CCID/smartcard interface is present in Device Manager\n"
                    "  - the Smart Card service is running\n"
                    "  - the correct smartcard/CCID driver is installed"
                )
            else:
                self.error_occurred.emit(
                    "No PCSC readers found.\n"
                    "Make sure the device is connected and pcscd is running:\n"
                    "  sudo systemctl start pcscd"
                )
            return False

        hint = ""
        if "6A82" in last_error:
            hint = (
                "\n\nSW=6A82 = Application Not Found.\n"
                "The PIV applet is not accessible on this device.\n\n"
                "Solo 2 default firmware includes: FIDO2, OATH, NDEF, Admin.\n"
                "PIV requires a firmware built with the 'develop-piv' feature.\n\n"
                "Use 'Diagnose PCSC' to check which applets respond.\n"
                "If OATH (A0000005272101) returns SW=9000, the CCID interface works\n"
                "but PIV is not compiled into your firmware."
            )
        self.error_occurred.emit(f"PIV applet not found. {last_error}{hint}")
        return False

    def _disconnect(self) -> None:
        """Disconnect from the device."""
        if self._connection:
            self._connection.close()
            self._connection = None
            self._selected = False

    def _send_apdu(
        self, ins: int, p1: int, p2: int, data: List[int] = None, le: int = 0
    ) -> Tuple[List[int], int, int]:
        """Send an APDU command and return response."""
        if not self._connection:
            raise Exception("Not connected")

        apdu = [0x00, ins, p1, p2]
        if data:
            if len(data) > 255:
                # Extended-length Lc (ISO 7816-4): 0x00 + 2-byte length
                apdu.extend([0x00, (len(data) >> 8) & 0xFF, len(data) & 0xFF])
            else:
                apdu.append(len(data))
            apdu.extend(data)
        if le > 0:
            apdu.append(le)

        response, sw1, sw2 = self._connection.transmit(apdu)
        return response, sw1, sw2

    def _encode_tlv(self, tag: int, value: List[int]) -> List[int]:
        """Encode a simple one-byte-tag TLV."""
        return [tag] + self._encode_length(len(value)) + value

    def _get_data(self, tag: List[int]) -> Optional[bytes]:
        """Get data object from PIV applet.

        GET DATA is a Case 4 command: it sends a tag selector and expects
        response data back.  Le=0x00 must be present so the card knows to
        return data; without it many PIV implementations return SW=9000 with
        zero bytes.  For objects larger than 256 bytes (e.g. certificates)
        the card may reply SW=61xx (more data); we chain GET RESPONSE calls
        until the full object is assembled.
        """
        data = [0x5C, len(tag)] + tag
        # Case 4 short APDU: CLA INS P1 P2 Lc data Le
        apdu = [0x00, INS_GET_DATA, 0x3F, 0xFF, len(data)] + data + [0x00]
        response, sw1, sw2 = self._connection.transmit(apdu)
        print(f"[PIV] GET DATA tag={[hex(b) for b in tag]} → SW={sw1:02X}{sw2:02X}, {len(response)} bytes")

        if sw1 == 0x61:
            # More data available — reassemble via GET RESPONSE chaining
            all_data = list(response)
            while sw1 == 0x61:
                remaining = sw2 if sw2 != 0x00 else 256
                get_resp = [0x00, 0xC0, 0x00, 0x00, remaining]
                response, sw1, sw2 = self._connection.transmit(get_resp)
                all_data.extend(response)
                print(f"[PIV] GET RESPONSE → SW={sw1:02X}{sw2:02X}, {len(response)} bytes")
            if sw1 == 0x90 and sw2 == 0x00:
                return bytes(all_data)
            return None

        if sw1 == 0x90 and sw2 == 0x00:
            return bytes(response)
        if sw1 == 0x6A and sw2 == 0x82:
            return None  # Data not found
        return None

    def _transmit_with_get_response(self, apdu: List[int]) -> Tuple[bytes, int, int]:
        """Send an APDU and collect any follow-up GET RESPONSE data."""
        if not self._connection:
            raise Exception("Not connected")

        response, sw1, sw2 = self._connection.transmit(apdu)
        data = bytearray(response)
        while sw1 == 0x61:
            remaining = sw2 if sw2 != 0x00 else 256
            response, sw1, sw2 = self._connection.transmit([0x00, 0xC0, 0x00, 0x00, remaining])
            data.extend(response)
        return bytes(data), sw1, sw2

    def _parse_simple_tlvs(self, data: bytes) -> Dict[int, bytes]:
        """Parse a flat one-byte-tag TLV sequence."""
        tlvs: Dict[int, bytes] = {}
        offset = 0
        while offset + 2 <= len(data):
            tag = data[offset]
            offset += 1
            first_len = data[offset]
            offset += 1
            if first_len & 0x80:
                len_len = first_len & 0x7F
                if offset + len_len > len(data):
                    break
                length = int.from_bytes(data[offset : offset + len_len], "big")
                offset += len_len
            else:
                length = first_len
            if offset + length > len(data):
                break
            tlvs[tag] = data[offset : offset + length]
            offset += length
        return tlvs

    def _key_type_from_metadata(self, metadata: bytes) -> Optional[str]:
        """Extract a human-readable key type from GET METADATA data."""
        try:
            tlvs = self._parse_simple_tlvs(metadata)
            algorithm = tlvs.get(0x01, b"")
            if not algorithm:
                return None
            alg_id = algorithm[0]
            return {
                ALGORITHM_ID[PivKeyType.RSA_2048]: _KEY_TYPE_LABELS[PivKeyType.RSA_2048],
                ALGORITHM_ID[PivKeyType.ECC_P256]: _KEY_TYPE_LABELS[PivKeyType.ECC_P256],
                ALGORITHM_ID[PivKeyType.ECC_P384]: _KEY_TYPE_LABELS[PivKeyType.ECC_P384],
            }.get(alg_id)
        except Exception:
            return None

    def _get_slot_metadata(self, slot: PivSlot) -> Tuple[bool, Optional[str]]:
        """Return whether the slot exposes real metadata and, if known, its key type."""
        if not self._connection:
            return False, None

        key_ref = KEY_REFERENCE.get(slot)
        if not key_ref:
            return False, None

        try:
            response, sw1, sw2 = self._transmit_with_get_response([0x00, 0xF7, 0x00, key_ref, 0x00])
            if sw1 != 0x90 or sw2 != 0x00:
                return False, None
            # On this firmware, empty slots can still return SW=9000 with an empty body.
            # Only treat the slot as populated when metadata TLVs are actually present.
            if not response:
                return False, None

            tlvs = self._parse_simple_tlvs(response)
            if not tlvs:
                return False, None

            # piv-authenticator v0.6.0 only implements GET METADATA for 9E and returns
            # a bare policy TLV there when the card-auth key exists.
            if slot == PivSlot.CARD_AUTH and tlvs == {0x02: b"\x01\x00"}:
                return True, None

            # For the other slots, treat the slot as populated only when we see
            # algorithm/origin/public-key data.
            has_real_metadata = any(tag in tlvs for tag in (0x01, 0x03, 0x04))
            if not has_real_metadata:
                return False, None

            return True, self._key_type_from_metadata(response)
        except Exception:
            return False, None

    def _slot_has_key(self, slot: PivSlot) -> bool:
        """Return True when GET METADATA exposes actual key metadata in the slot."""
        has_key, _key_type = self._get_slot_metadata(slot)
        return has_key

    def _build_sign_probe_data(self, key_type: PivKeyType) -> List[int]:
        """Build a GENERAL AUTHENTICATE sign probe for the requested key type."""
        if key_type == PivKeyType.ECC_P256:
            digest = list(bytes(range(1, 33)))
        elif key_type == PivKeyType.ECC_P384:
            digest = list(bytes(range(1, 49)))
        elif key_type == PivKeyType.RSA_2048:
            digest_info_prefix = bytes.fromhex("3031300d060960864801650304020105000420")
            digest = bytes(range(1, 33))
            digest_block = digest_info_prefix + digest
            padding_len = 256 - 3 - len(digest_block)
            digest = [0x00, 0x01] + ([0xFF] * padding_len) + [0x00] + list(digest_block)
        else:
            raise ValueError(f"Unsupported probe key type: {key_type}")

        auth_template = self._encode_tlv(0x82, []) + self._encode_tlv(0x81, digest)
        return self._encode_tlv(0x7C, auth_template)

    def _probe_slot_with_verified_pin(self, slot: PivSlot) -> Optional[PivKeyType]:
        """Try to prove key presence in a slot after PIN verification."""
        key_ref = KEY_REFERENCE.get(slot)
        if not key_ref:
            return None

        for key_type in (PivKeyType.ECC_P256, PivKeyType.ECC_P384, PivKeyType.RSA_2048):
            try:
                response, sw1, sw2 = self._send_apdu(
                    INS_AUTHENTICATE,
                    ALGORITHM_ID[key_type],
                    key_ref,
                    self._build_sign_probe_data(key_type),
                )
            except Exception:
                continue

            # On piv-authenticator v0.6.0, a successful GENERAL AUTHENTICATE probe is
            # the only reliable positive signal for 9A/9C/9D. Error codes such as 6985
            # are also used for empty slots, so they must not be treated as proof.
            if (sw1 == 0x90 and sw2 == 0x00) or sw1 == 0x61:
                return key_type

        return None

    def _collect_slot_infos(self) -> List[SlotInfo]:
        """Collect combined slot state using the current connection."""
        result = []
        for slot in PivSlot:
            cert = None
            tag = TAG_CERTIFICATE.get(slot)
            if tag:
                raw = self._get_data(tag)
                if raw:
                    cert = self._parse_certificate(raw, slot)

            cached = self._key_cache.get(slot)
            metadata_key, metadata_key_type = self._get_slot_metadata(slot)
            has_key = bool(metadata_key or cert is not None or cached is not None)

            key_type_str = None
            if cached:
                key_type_str = cached.get("algorithm")
            elif metadata_key_type:
                key_type_str = metadata_key_type
            elif cert:
                key_type_str = self._detect_key_type_from_cert(cert.certificate_der)

            result.append(SlotInfo(slot, has_key, key_type_str, cert))
        return result

    def _parse_certificate(self, data: bytes, slot: PivSlot) -> Optional[PivCertificate]:
        """Parse a PIV certificate from raw data."""
        try:
            # Skip the outer TLV wrapper (tag 0x53 for compressed cert)
            if data[0] == 0x53:
                # Parse length
                offset = 1
                if data[offset] & 0x80:
                    len_bytes = data[offset] & 0x7F
                    offset += 1 + len_bytes
                else:
                    offset += 1

                # Get the certificate (tag 0x70)
                if data[offset] == 0x70:
                    offset += 1
                    if data[offset] & 0x80:
                        len_bytes = data[offset] & 0x7F
                        cert_len = int.from_bytes(
                            data[offset + 1 : offset + 1 + len_bytes], "big"
                        )
                        offset += 1 + len_bytes
                    else:
                        cert_len = data[offset]
                        offset += 1

                    cert_der = data[offset : offset + cert_len]
                else:
                    cert_der = data
            else:
                cert_der = data

            # Try to parse with cryptography library
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend

                cert = x509.load_der_x509_certificate(cert_der, default_backend())
                return PivCertificate(
                    slot=slot,
                    subject=cert.subject.rfc4514_string(),
                    issuer=cert.issuer.rfc4514_string(),
                    serial=str(cert.serial_number),
                    not_before=cert.not_valid_before_utc.isoformat(),
                    not_after=cert.not_valid_after_utc.isoformat(),
                    certificate_der=cert_der,
                )
            except ImportError:
                # cryptography not available, return basic info
                return PivCertificate(
                    slot=slot,
                    subject="(certificate parsing unavailable)",
                    issuer="(certificate parsing unavailable)",
                    serial="",
                    not_before="",
                    not_after="",
                    certificate_der=cert_der,
                )

        except Exception as e:
            return None

    def probe_piv(self) -> None:
        """Silently probe whether the PIV applet is present. Emits piv_probed(bool).

        Uses the same 9-byte AID + Le=0x00 SELECT that solo2-cli uses.
        Does NOT emit error_occurred so the tab can stay silent when PIV is absent.

        Retries up to 5 times with 500 ms delay: the CCID interface comes up
        later than HID after a device plug-in event.
        """
        if not PCSC_AVAILABLE:
            self.piv_probed.emit(False)
            return

        MID_AID = [0xA0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00]
        select_cmd = [0x00, INS_SELECT, 0x04, 0x00, len(MID_AID)] + MID_AID + [0x00]

        for attempt in range(6):
            if attempt > 0:
                time.sleep(0.5)
            for connection in iter_pcsc_connections():
                try:
                    _resp, sw1, sw2 = connection.transmit(select_cmd)
                    connection.close()
                    if (sw1 == 0x90 and sw2 == 0x00) or sw1 == 0x61:
                        self.piv_probed.emit(True)
                        return
                except Exception:
                    connection.close()

        self.piv_probed.emit(False)

    def load_keys(self) -> None:
        """Load all PIV keys from the device using YubiKey GetMetadata command."""
        if not self.check_pcsc_available():
            self.keys_loaded.emit([])
            return

        if not self._connect():
            self.keys_loaded.emit([])
            return

        try:
            keys = []
            print(f"[PIV] Loading keys...")

            # Check each slot using YubiKey GetMetadata command (0xF7)
            for slot in PivSlot:
                key_ref = KEY_REFERENCE.get(slot)
                if not key_ref:
                    continue

                print(f"[PIV] Checking slot {slot.name} ({key_ref:02X})...")

                try:
                    has_key, key_type_str = self._get_slot_metadata(slot)

                    if has_key:
                        print(f"[PIV]   ✓ Key exists")
                        
                        tag = TAG_CERTIFICATE.get(slot)
                        cert_data = self._get_data(tag) if tag else None
                        
                        key = PivKey(
                            slot=slot,
                            key_type=None,
                            algorithm=key_type_str or "Unknown",
                            has_certificate=cert_data is not None,
                        )
                        keys.append(key)
                    else:
                        print(f"[PIV]   Empty")
                        
                except Exception as e:
                    print(f"[PIV]   Error: {e}")

            print(f"[PIV] Total keys found: {len(keys)}")
            self.keys_loaded.emit(keys)

        except Exception as e:
            print(f"[PIV] Error loading keys: {e}")
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(f"Failed to load keys: {e}")
            self.keys_loaded.emit([])
        finally:
            self._disconnect()

    def load_certificates(self) -> None:
        """Load all PIV certificates from the device."""
        if not self.check_pcsc_available():
            self.certificates_loaded.emit([])
            return

        if not self._connect():
            self.certificates_loaded.emit([])
            return

        try:
            certificates = []

            for slot in PivSlot:
                tag = TAG_CERTIFICATE.get(slot)
                if not tag:
                    continue

                cert_data = self._get_data(tag)
                if cert_data:
                    cert = self._parse_certificate(cert_data, slot)
                    if cert:
                        certificates.append(cert)

            self.certificates_loaded.emit(certificates)

        except Exception as e:
            self.error_occurred.emit(f"Failed to load certificates: {e}")
            self.certificates_loaded.emit([])
        finally:
            self._disconnect()

    def load_slots(self) -> None:
        """Load combined key+cert state for all 4 PIV slots. Emits slots_loaded(list[SlotInfo])."""
        empty = [SlotInfo(s, False, None, None) for s in PivSlot]

        if not self.check_pcsc_available():
            self.slots_loaded.emit(empty)
            return
        if not self._connect():
            self.slots_loaded.emit(empty)
            return
        try:
            self.slots_loaded.emit(self._collect_slot_infos())
        except Exception as e:
            self.error_occurred.emit(f"Failed to load slots: {e}")
            self.slots_loaded.emit(empty)
        finally:
            self._disconnect()

    def probe_slots_with_pin(self, pin: str) -> None:
        """Verify the PIN once and actively probe slots that do not expose metadata."""
        empty = [SlotInfo(s, False, None, None) for s in PivSlot]

        if not self.check_pcsc_available():
            self.key_probe_completed.emit(False, "PCSC not available")
            return
        if not self._connect():
            self.key_probe_completed.emit(False, "Failed to connect to device")
            self.slots_loaded.emit(empty)
            return

        try:
            pin_ok, pin_message = self._verify_pin_with_status(pin)
            if not pin_ok:
                self.key_probe_completed.emit(False, pin_message or "PIN verification failed")
                return

            discovered = 0
            for slot in PivSlot:
                cert = None
                tag = TAG_CERTIFICATE.get(slot)
                if tag:
                    raw = self._get_data(tag)
                    if raw:
                        cert = self._parse_certificate(raw, slot)
                metadata_key, metadata_key_type = self._get_slot_metadata(slot)
                if metadata_key or cert is not None:
                    if metadata_key and metadata_key_type:
                        self._key_cache[slot] = {
                            "key_type": None,
                            "algorithm": metadata_key_type,
                            "has_certificate": cert is not None,
                        }
                    continue

                detected_key_type = self._probe_slot_with_verified_pin(slot)
                if detected_key_type is None:
                    continue

                discovered += 1
                self._key_cache[slot] = {
                    "key_type": detected_key_type,
                    "algorithm": _KEY_TYPE_LABELS.get(detected_key_type, detected_key_type.value),
                    "has_certificate": False,
                }

            self.slots_loaded.emit(self._collect_slot_infos())
            if discovered:
                noun = "slot" if discovered == 1 else "slots"
                self.key_probe_completed.emit(
                    True,
                    f"PIN verified. Detected additional private keys in {discovered} {noun}.",
                )
            else:
                self.key_probe_completed.emit(
                    True,
                    "PIN verified. No additional hidden PIV keys were detected.",
                )
        except Exception as e:
            self.error_occurred.emit(f"Failed to probe slots with PIN: {e}")
            self.slots_loaded.emit(empty)
            self.key_probe_completed.emit(False, str(e))
        finally:
            self._disconnect()

    def _detect_key_type_from_cert(self, cert_der: bytes) -> Optional[str]:
        """Detect key type string from a DER certificate's public key."""
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.asymmetric import ec, rsa

            cert = x509.load_der_x509_certificate(cert_der, default_backend())
            pub = cert.public_key()
            if isinstance(pub, ec.EllipticCurvePublicKey):
                return {'secp256r1': 'ECC P-256', 'secp384r1': 'ECC P-384'}.get(
                    pub.curve.name, f'ECC ({pub.curve.name})'
                )
            if isinstance(pub, rsa.RSAPublicKey):
                return f'RSA {pub.key_size}'
        except Exception:
            pass
        return None

    def generate_key(
        self,
        slot: PivSlot,
        key_type: PivKeyType,
        pin: Optional[str] = None,
        mgmt_key: str = DEFAULT_MANAGEMENT_KEY,
    ) -> None:
        """Generate a new PIV key in the specified slot."""
        if not self.check_pcsc_available():
            self.key_generated.emit(False, "PCSC not available", b"", None)
            return

        if not self._connect():
            self.key_generated.emit(False, "Failed to connect to device", b"", None)
            return

        try:
            # Authenticate with management key (required for key generation)
            if not self._authenticate_management_key(mgmt_key):
                self.key_generated.emit(False, "Management key authentication failed", b"", None)
                return

            # Verify PIN (required for 9A/9C/9D slots)
            if pin and slot != PivSlot.CARD_AUTH:
                if not self._verify_pin(pin):
                    self.key_generated.emit(False, "PIN verification failed", b"", None)
                    return

            key_ref = KEY_REFERENCE.get(slot)
            alg_id = ALGORITHM_ID.get(key_type)

            if not key_ref or not alg_id:
                self.key_generated.emit(False, "Invalid slot or key type", b"", None)
                return

            template = [0xAC, 0x03, 0x80, 0x01, alg_id]

            response, sw1, sw2 = self._send_apdu(
                INS_GENERATE_ASYMMETRIC, 0x00, key_ref, template, 0x00
            )

            if sw1 == 0x90 and sw2 == 0x00:
                pubkey_der = self._extract_pubkey_der(response, key_type)
                # Cache the key info for detection
                self._key_cache[slot] = {
                    'key_type': key_type,
                    'algorithm': _KEY_TYPE_LABELS.get(key_type, key_type.value if key_type else "Unknown"),
                    'has_certificate': False,
                }
                self.key_generated.emit(True, "", pubkey_der, slot)
            elif sw1 == 0x61:
                # More data available - need to call GET RESPONSE
                print(f"[PIV] Key generated, retrieving public key ({sw2} bytes)...")
                get_response = [0x00, 0xC0, 0x00, 0x00, sw2]
                response2, sw1_2, sw2_2 = self._connection.transmit(get_response)
                if sw1_2 == 0x90 and sw2_2 == 0x00:
                    pubkey_der = self._extract_pubkey_der(response2, key_type)
                    # Cache the key info for detection
                    self._key_cache[slot] = {
                        'key_type': key_type,
                        'algorithm': _KEY_TYPE_LABELS.get(key_type, key_type.value if key_type else "Unknown"),
                        'has_certificate': False,
                    }
                    self.key_generated.emit(True, "", pubkey_der, slot)
                else:
                    self.key_generated.emit(False, f"Failed to retrieve public key: SW={sw1_2:02X}{sw2_2:02X}", b"", None)
            elif sw1 == 0x69 and sw2 == 0x82:
                self.key_generated.emit(False, "Security status not satisfied", b"", None)
            else:
                self.key_generated.emit(False, f"Generation failed: SW={sw1:02X}{sw2:02X}", b"", None)

        except Exception as e:
            self.key_generated.emit(False, str(e), b"", None)
        finally:
            self._disconnect()

    def delete_key(self, slot: PivSlot) -> None:
        """Delete a PIV key by overwriting with empty data."""
        # PIV doesn't have a direct "delete key" command
        # The key is removed by deleting the certificate
        self.delete_certificate(slot)

    def import_certificate(
        self,
        slot: PivSlot,
        certificate_data: bytes,
        pin: Optional[str] = None,
        mgmt_key: str = DEFAULT_MANAGEMENT_KEY,
    ) -> None:
        """Import a certificate to a slot."""
        if not self.check_pcsc_available():
            self.certificate_imported.emit(False, "PCSC not available")
            return

        if not self._connect():
            self.certificate_imported.emit(False, "Failed to connect to device")
            return

        try:
            # Authenticate with management key (required for PUT DATA)
            if not self._authenticate_management_key(mgmt_key):
                self.certificate_imported.emit(False, "Management key authentication failed")
                return

            if pin:
                if not self._verify_pin(pin):
                    self.certificate_imported.emit(False, "PIN verification failed")
                    return

            tag = TAG_CERTIFICATE.get(slot)
            if not tag:
                self.certificate_imported.emit(False, "Invalid slot")
                return

            # Build PUT DATA command
            # Certificate is wrapped: 53 len { 70 len <cert> 71 01 00 FE 00 }
            cert_tlv = [0x70] + self._encode_length(len(certificate_data))
            cert_tlv.extend(certificate_data)
            cert_tlv.extend([0x71, 0x01, 0x00])  # Compression: uncompressed
            cert_tlv.extend([0xFE, 0x00])  # LRC

            data_obj = [0x53] + self._encode_length(len(cert_tlv)) + cert_tlv

            # Add tag to data
            full_data = [0x5C, len(tag)] + tag + data_obj

            response, sw1, sw2 = self._send_apdu(INS_PUT_DATA, 0x3F, 0xFF, full_data)

            if sw1 == 0x90 and sw2 == 0x00:
                self.certificate_imported.emit(True, "")
            else:
                self.certificate_imported.emit(
                    False, f"Import failed: SW={sw1:02X}{sw2:02X}"
                )

        except Exception as e:
            self.certificate_imported.emit(False, str(e))
        finally:
            self._disconnect()

    def export_certificate(self, slot: PivSlot) -> None:
        """Export a certificate from a slot."""
        if not self.check_pcsc_available():
            self.certificate_exported.emit(False, "PCSC not available", b"")
            return

        if not self._connect():
            self.certificate_exported.emit(False, "Failed to connect", b"")
            return

        try:
            tag = TAG_CERTIFICATE.get(slot)
            if not tag:
                self.certificate_exported.emit(False, "Invalid slot", b"")
                return

            cert_data = self._get_data(tag)
            if cert_data:
                cert = self._parse_certificate(cert_data, slot)
                if cert:
                    self.certificate_exported.emit(True, "", cert.certificate_der)
                else:
                    self.certificate_exported.emit(False, "Failed to parse certificate", b"")
            else:
                self.certificate_exported.emit(False, "No certificate in slot", b"")

        except Exception as e:
            self.certificate_exported.emit(False, str(e), b"")
        finally:
            self._disconnect()

    def delete_certificate(self, slot: PivSlot, pin: Optional[str] = None) -> None:
        """Delete a certificate from a slot."""
        if not self.check_pcsc_available():
            self.key_deleted.emit(False, "PCSC not available")
            return

        if not self._connect():
            self.key_deleted.emit(False, "Failed to connect to device")
            return

        try:
            if pin:
                if not self._verify_pin(pin):
                    self.key_deleted.emit(False, "PIN verification failed")
                    return

            tag = TAG_CERTIFICATE.get(slot)
            if not tag:
                self.key_deleted.emit(False, "Invalid slot")
                return

            # PUT DATA with empty data to delete
            full_data = [0x5C, len(tag)] + tag + [0x53, 0x00]

            response, sw1, sw2 = self._send_apdu(INS_PUT_DATA, 0x3F, 0xFF, full_data)

            if sw1 == 0x90 and sw2 == 0x00:
                self.key_deleted.emit(True, "")
            else:
                self.key_deleted.emit(False, f"Delete failed: SW={sw1:02X}{sw2:02X}")

        except Exception as e:
            self.key_deleted.emit(False, str(e))
        finally:
            self._disconnect()

    def _verify_pin(self, pin: str) -> bool:
        """Verify the PIV PIN."""
        success, _message = self._verify_pin_with_status(pin)
        return success

    def _verify_pin_with_status(self, pin: str) -> Tuple[bool, str]:
        """Verify the PIV PIN and return a user-facing status message on failure."""
        pin_bytes = pin.encode("utf-8")
        # Pad to 8 bytes with 0xFF
        pin_data = list(pin_bytes) + [0xFF] * (8 - len(pin_bytes))

        response, sw1, sw2 = self._send_apdu(INS_VERIFY, 0x00, 0x80, pin_data)
        if sw1 == 0x90 and sw2 == 0x00:
            return True, ""
        if sw1 == 0x63 and (sw2 & 0xF0) == 0xC0:
            retries = sw2 & 0x0F
            return False, f"Incorrect PIN ({retries} retries remaining)"
        if sw1 == 0x69 and sw2 == 0x83:
            return False, "PIN is blocked"
        return False, f"PIN verification failed: SW={sw1:02X}{sw2:02X}"

    def _authenticate_management_key(self, mgmt_key_hex: str) -> bool:
        """Authenticate with the PIV management key (3DES challenge-response).

        Standard PIV GENERAL AUTHENTICATE, algorithm 3DES (0x03), key ref 0x9B.
        """
        try:
            key_bytes = bytes.fromhex(mgmt_key_hex.replace(" ", "").replace(":", ""))
            if len(key_bytes) != 24:
                return False
        except ValueError:
            return False

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            return False

        # Step 1: request challenge from card
        req = [0x7C, 0x02, 0x81, 0x00]
        response, sw1, sw2 = self._send_apdu(INS_AUTHENTICATE, 0x03, 0x9B, req, 0xFF)
        if sw1 != 0x90 and sw1 != 0x61:
            return False

        # Parse 7C xx 81 08 <8 bytes challenge>
        resp_bytes = bytes(response)
        if len(resp_bytes) < 12 or resp_bytes[0] != 0x7C or resp_bytes[2] != 0x81:
            return False
        challenge = resp_bytes[4:12]

        # Step 2: encrypt challenge with 3DES-ECB
        cipher = Cipher(
            algorithms.TripleDES(key_bytes), modes.ECB(), backend=default_backend()
        )
        enc = cipher.encryptor()
        encrypted = enc.update(challenge) + enc.finalize()

        # Step 3: send encrypted response
        resp_data = [0x7C, 0x0A, 0x82, 0x08] + list(encrypted)
        response, sw1, sw2 = self._send_apdu(INS_AUTHENTICATE, 0x03, 0x9B, resp_data, 0x00)
        return sw1 == 0x90 and sw2 == 0x00

    def _extract_pubkey_der(self, response: List[int], key_type: PivKeyType) -> bytes:
        """Extract DER-encoded SubjectPublicKeyInfo from GENERATE ASYMMETRIC response."""
        try:
            from cryptography.hazmat.primitives.asymmetric.ec import (
                EllipticCurvePublicKey, SECP256R1, SECP384R1, EllipticCurvePublicNumbers
            )
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            from cryptography.hazmat.backends import default_backend

            data = bytes(response)
            # Response is: 7F 49 len { 86 len <point_bytes> }
            # Find tag 0x86 (BIT STRING for EC point) or 0x81 (modulus for RSA)
            idx = 0
            # Skip outer 7F 49 wrapper
            if len(data) >= 2 and data[0] == 0x7F and data[1] == 0x49:
                idx = 2
                # skip length
                if data[idx] & 0x80:
                    idx += 1 + (data[idx] & 0x7F)
                else:
                    idx += 1

            # Find tag 0x86 (EC point)
            while idx < len(data) - 1:
                tag = data[idx]
                idx += 1
                if data[idx] & 0x80:
                    ll = data[idx] & 0x7F
                    length = int.from_bytes(data[idx + 1: idx + 1 + ll], "big")
                    idx += 1 + ll
                else:
                    length = data[idx]
                    idx += 1
                value = data[idx: idx + length]
                idx += length

                if tag == 0x86 and key_type in (PivKeyType.ECC_P256, PivKeyType.ECC_P384):
                    curve = SECP256R1() if key_type == PivKeyType.ECC_P256 else SECP384R1()
                    coord_len = 32 if key_type == PivKeyType.ECC_P256 else 48
                    # value is 04 || x || y
                    if len(value) == 1 + 2 * coord_len and value[0] == 0x04:
                        x = int.from_bytes(value[1: 1 + coord_len], "big")
                        y = int.from_bytes(value[1 + coord_len:], "big")
                        pub = EllipticCurvePublicNumbers(x, y, curve).public_key(default_backend())
                        return pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        except Exception:
            pass
        return b""

    def _encode_length(self, length: int) -> List[int]:
        """Encode length in DER format."""
        if length < 128:
            return [length]
        elif length < 256:
            return [0x81, length]
        else:
            return [0x82, (length >> 8) & 0xFF, length & 0xFF]

    def get_pin_status(self) -> None:
        """Get current PIN status and retry counters."""
        if not self.check_pcsc_available():
            self.pin_status_updated.emit(
                {"pcsc_available": False, "pin_retries": None, "puk_retries": None}
            )
            return

        if not self._connect():
            self.pin_status_updated.emit(
                {"pcsc_available": True, "connected": False}
            )
            return

        try:
            # Try to verify with empty PIN to get retry count
            response, sw1, sw2 = self._send_apdu(INS_VERIFY, 0x00, 0x80, [])

            pin_retries = None
            if sw1 == 0x63 and (sw2 & 0xF0) == 0xC0:
                pin_retries = sw2 & 0x0F

            # Try PUK as well
            response, sw1, sw2 = self._send_apdu(INS_VERIFY, 0x00, 0x81, [])

            puk_retries = None
            if sw1 == 0x63 and (sw2 & 0xF0) == 0xC0:
                puk_retries = sw2 & 0x0F

            status = {
                "pcsc_available": True,
                "connected": True,
                "pin_retries": pin_retries,
                "puk_retries": puk_retries,
            }

            self.pin_status_updated.emit(status)

        except Exception as e:
            self.error_occurred.emit(f"Failed to get PIN status: {e}")
        finally:
            self._disconnect()

    def change_pin(self, current_pin: str, new_pin: str) -> None:
        """Change the PIV PIN."""
        if not self.check_pcsc_available():
            self.pin_changed.emit(False, "PCSC not available")
            return

        if len(new_pin) < 6 or len(new_pin) > 8:
            self.pin_changed.emit(False, "PIN must be 6-8 characters")
            return

        if not self._connect():
            self.pin_changed.emit(False, "Failed to connect to device")
            return

        try:
            # Build PIN change data: old PIN (8 bytes) + new PIN (8 bytes)
            old_pin_bytes = current_pin.encode("utf-8")
            new_pin_bytes = new_pin.encode("utf-8")

            pin_data = list(old_pin_bytes) + [0xFF] * (8 - len(old_pin_bytes))
            pin_data.extend(list(new_pin_bytes) + [0xFF] * (8 - len(new_pin_bytes)))

            response, sw1, sw2 = self._send_apdu(
                INS_CHANGE_REFERENCE_DATA, 0x00, 0x80, pin_data
            )

            if sw1 == 0x90 and sw2 == 0x00:
                self.pin_changed.emit(True, "")
            elif sw1 == 0x63:
                retries = sw2 & 0x0F if (sw2 & 0xF0) == 0xC0 else "unknown"
                self.pin_changed.emit(
                    False, f"Incorrect current PIN ({retries} retries remaining)"
                )
            elif sw1 == 0x69 and sw2 == 0x83:
                self.pin_changed.emit(False, "PIN is blocked")
            else:
                self.pin_changed.emit(False, f"PIN change failed: SW={sw1:02X}{sw2:02X}")

        except Exception as e:
            self.pin_changed.emit(False, str(e))
        finally:
            self._disconnect()

    def unblock_pin(self, puk: str, new_pin: str) -> None:
        """Unblock the PIV PIN using PUK."""
        if not self.check_pcsc_available():
            self.pin_changed.emit(False, "PCSC not available")
            return

        if not self._connect():
            self.pin_changed.emit(False, "Failed to connect to device")
            return

        try:
            # Build unblock data: PUK (8 bytes) + new PIN (8 bytes)
            puk_bytes = puk.encode("utf-8")
            new_pin_bytes = new_pin.encode("utf-8")

            data = list(puk_bytes) + [0xFF] * (8 - len(puk_bytes))
            data.extend(list(new_pin_bytes) + [0xFF] * (8 - len(new_pin_bytes)))

            response, sw1, sw2 = self._send_apdu(
                INS_RESET_RETRY_COUNTER, 0x00, 0x80, data
            )

            if sw1 == 0x90 and sw2 == 0x00:
                self.pin_changed.emit(True, "PIN unblocked successfully")
            elif sw1 == 0x63:
                retries = sw2 & 0x0F if (sw2 & 0xF0) == 0xC0 else "unknown"
                self.pin_changed.emit(
                    False, f"Incorrect PUK ({retries} retries remaining)"
                )
            elif sw1 == 0x69 and sw2 == 0x83:
                self.pin_changed.emit(False, "PUK is blocked - device must be reset")
            else:
                self.pin_changed.emit(False, f"Unblock failed: SW={sw1:02X}{sw2:02X}")

        except Exception as e:
            self.pin_changed.emit(False, str(e))
        finally:
            self._disconnect()

    def change_puk(self, current_puk: str, new_puk: str) -> None:
        """Change the PIV PUK."""
        if not self.check_pcsc_available():
            self.pin_changed.emit(False, "PCSC not available")
            return

        if not self._connect():
            self.pin_changed.emit(False, "Failed to connect to device")
            return

        try:
            # Build PUK change data
            old_puk_bytes = current_puk.encode("utf-8")
            new_puk_bytes = new_puk.encode("utf-8")

            data = list(old_puk_bytes) + [0xFF] * (8 - len(old_puk_bytes))
            data.extend(list(new_puk_bytes) + [0xFF] * (8 - len(new_puk_bytes)))

            response, sw1, sw2 = self._send_apdu(
                INS_CHANGE_REFERENCE_DATA, 0x00, 0x81, data
            )

            if sw1 == 0x90 and sw2 == 0x00:
                self.pin_changed.emit(True, "PUK changed successfully")
            else:
                self.pin_changed.emit(False, f"PUK change failed: SW={sw1:02X}{sw2:02X}")

        except Exception as e:
            self.pin_changed.emit(False, str(e))
        finally:
            self._disconnect()

    def diagnose_pcsc(self) -> None:
        """Run PCSC diagnostic: probe known AIDs and report SW codes.

        Emits diagnose_result(str) with a human-readable report.
        """
        if not PCSC_AVAILABLE:
            self.diagnose_result.emit(
                "PCSC not available.\n"
                "Install: sudo apt install pcscd pcsc-tools && pip install pyscard"
            )
            return

        reader_names = list_pcsc_reader_names()
        if not reader_names:
            self.diagnose_result.emit(
                "No PCSC readers found.\n"
                "Make sure pcscd is running: sudo systemctl start pcscd"
            )
            return

        # Known AIDs to probe.
        # OATH (A000000527 2101) is in Solo 2 default firmware — if it returns 9000,
        # the CCID/apdu-dispatch pipeline works and other 6A82 errors mean those apps
        # are simply not compiled into this firmware build.
        KNOWN_AIDS = [
            ("OATH/TOTP (default firmware)",  [0xA0, 0x00, 0x00, 0x05, 0x27, 0x21, 0x01]),
            ("PIV (9 bytes + Le, solo2-cli)", [0xA0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00]),
            ("PIV (full, 11 bytes)",          [0xA0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00, 0x01, 0x00]),
            ("Provision app",                 [0xA0, 0x00, 0x00, 0x08, 0x47, 0x01, 0x00, 0x00, 0x01]),
            ("OpenPGP",                       [0xD2, 0x76, 0x00, 0x01, 0x24, 0x01]),
            ("NDEF",                          [0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01]),
        ]

        lines = [f"PCSC diagnostic — {len(reader_names)} reader(s) found\n"]

        for connection in iter_pcsc_connections():
            lines.append(f"Reader: {connection.reader_name} (connected)")
            for app_name, aid_bytes in KNOWN_AIDS:
                # Always send Case 4 (with Le=0x00) — this is what solo2-cli uses
                select_cmd = [0x00, INS_SELECT, 0x04, 0x00, len(aid_bytes)] + aid_bytes + [0x00]
                try:
                    _resp, sw1, sw2 = connection.transmit(select_cmd)
                    status = "OK" if sw1 == 0x90 else ("FCI pending" if sw1 == 0x61 else "not found" if (sw1 == 0x6A and sw2 == 0x82) else "error")
                    lines.append(f"  SELECT {app_name}: SW={sw1:02X}{sw2:02X} ({status})")
                except Exception as e:
                    lines.append(f"  SELECT {app_name}: exception — {e}")
            connection.close()

        self.diagnose_result.emit("\n".join(lines))

    def reset_piv(self) -> None:
        """Reset the PIV applet to factory defaults.

        Requires both PIN and PUK to be blocked (0 retries remaining).
        Uses INS=0xFB (standard Yubico PIV reset extension).
        """
        if not self.check_pcsc_available():
            self.reset_completed.emit(False, "PCSC not available")
            return

        if not self._connect():
            self.reset_completed.emit(False, "Failed to connect to device")
            return

        try:
            response, sw1, sw2 = self._send_apdu(INS_RESET_PIV, 0x00, 0x00)
            if sw1 == 0x90 and sw2 == 0x00:
                self.reset_completed.emit(True, "PIV applet reset to factory defaults")
            else:
                self.reset_completed.emit(False, f"Reset failed: SW={sw1:02X}{sw2:02X}")
        except Exception as e:
            self.reset_completed.emit(False, str(e))
        finally:
            self._disconnect()
