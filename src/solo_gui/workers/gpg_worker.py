"""GPG (OpenPGP) worker thread for SoloKeys GUI.

OpenPGP card functionality uses the CCID/smartcard interface (PCSC).
This worker attempts to use pyscard for PCSC communication, but gracefully
handles the case when it's not available.

To enable full OpenPGP functionality:
1. Install PCSC daemon: sudo apt install pcscd pcsc-tools
2. Install pyscard: pip install pyscard
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import base64
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import time
import platform

from PySide6.QtCore import QObject, Signal

from solo2.pcsc import (
    PCSC_AVAILABLE,
    PCSC_IMPORT_ERROR,
    iter_pcsc_connections,
)


# OpenPGP Application AID (standard, ISO 7816-5)
GPG_AID = [0xD2, 0x76, 0x00, 0x01, 0x24, 0x01]

# Instructions
INS_SELECT = 0xA4
INS_GET_DATA = 0xCA
INS_PUT_DATA = 0xDA
INS_PUT_DATA_ODD = 0xDB
INS_VERIFY = 0x20
INS_CHANGE_REFERENCE_DATA = 0x24
INS_PSO = 0x2A
INS_TERMINATE_DF = 0xE6
INS_ACTIVATE_FILE = 0x44
INS_GENERATE_ASYM_KEY = 0x47


class GpgKeySlot(Enum):
    SIGN = "sign"
    DECRYPT = "decrypt"
    AUTH = "auth"


@dataclass
class GpgKeyInfo:
    slot: GpgKeySlot
    has_key: bool
    fingerprint: Optional[str]  # 40-char hex or None
    algo: Optional[str]         # e.g. "Ed25519", "NIST P-256"
    created: Optional[str]      # ISO date string or None


@dataclass
class GpgImportCandidate:
    keygrip: str
    fingerprint: str
    keyid: str
    user_id: str
    algorithm: str
    capabilities: str
    is_primary: bool
    created: Optional[str]

    def display_label(self) -> str:
        role = "primary" if self.is_primary else "subkey"
        caps = "".join(sorted(set(self.capabilities.lower())))
        parts = [self.user_id or self.fingerprint, self.algorithm, role]
        if caps:
            parts.append(f"caps {caps}")
        parts.append(f"…{self.fingerprint[-8:]}")
        return "  ·  ".join(parts)


@dataclass(frozen=True)
class OpenPgpAlgoSpec:
    key: str
    label: str
    sign_attrs: Optional[Tuple[int, ...]]
    decrypt_attrs: Optional[Tuple[int, ...]]
    aliases: Tuple[str, ...]
    kdf_hash_id: Optional[int] = None
    kdf_sym_id: Optional[int] = None
    is_25519: bool = False


# Algorithm attribute bytes for PUT DATA (tags C1/C2/C3)
# Format: first byte = algo ID
_ALGO_ID_EDDSA = 0x16
_ALGO_ID_ECDH = 0x12
_ALGO_ID_ECDSA = 0x13
_ALGO_ID_RSA = 0x01

_OPENPGP_ALGO_SPECS: Tuple[OpenPgpAlgoSpec, ...] = (
    OpenPgpAlgoSpec(
        key="Ed25519",
        label="Ed25519",
        sign_attrs=(0x16, 0x2B, 0x06, 0x01, 0x04, 0x01, 0xDA, 0x47, 0x0F, 0x01),
        decrypt_attrs=None,
        aliases=("ed25519", "eddsa"),
        is_25519=True,
    ),
    OpenPgpAlgoSpec(
        key="Cv25519",
        label="Cv25519 / X25519",
        sign_attrs=None,
        decrypt_attrs=(0x12, 0x2B, 0x06, 0x01, 0x04, 0x01, 0x97, 0x55, 0x01, 0x05, 0x01),
        aliases=("cv25519", "x25519", "curve25519"),
        kdf_hash_id=0x08,
        kdf_sym_id=0x09,
        is_25519=True,
    ),
    OpenPgpAlgoSpec(
        key="P-256",
        label="NIST P-256",
        sign_attrs=(0x13, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07),
        decrypt_attrs=(0x12, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07),
        aliases=("p256", "nistp256", "nist p256", "nist p-256", "secp256r1", "prime256v1"),
        kdf_hash_id=0x08,
        kdf_sym_id=0x07,
    ),
    OpenPgpAlgoSpec(
        key="P-384",
        label="NIST P-384",
        sign_attrs=(0x13, 0x2B, 0x81, 0x04, 0x00, 0x22),
        decrypt_attrs=(0x12, 0x2B, 0x81, 0x04, 0x00, 0x22),
        aliases=("p384", "nistp384", "nist p384", "nist p-384", "secp384r1"),
        kdf_hash_id=0x09,
        kdf_sym_id=0x08,
    ),
    OpenPgpAlgoSpec(
        key="P-521",
        label="NIST P-521",
        sign_attrs=(0x13, 0x2B, 0x81, 0x04, 0x00, 0x23),
        decrypt_attrs=(0x12, 0x2B, 0x81, 0x04, 0x00, 0x23),
        aliases=("p521", "nistp521", "nist p521", "nist p-521", "secp521r1"),
        kdf_hash_id=0x0A,
        kdf_sym_id=0x09,
    ),
    OpenPgpAlgoSpec(
        key="BrainpoolP256R1",
        label="Brainpool P256R1",
        sign_attrs=(0x13, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x07),
        decrypt_attrs=(0x12, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x07),
        aliases=("brainpoolp256r1", "brainpool p256r1"),
        kdf_hash_id=0x08,
        kdf_sym_id=0x07,
    ),
    OpenPgpAlgoSpec(
        key="BrainpoolP384R1",
        label="Brainpool P384R1",
        sign_attrs=(0x13, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0B),
        decrypt_attrs=(0x12, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0B),
        aliases=("brainpoolp384r1", "brainpool p384r1"),
        kdf_hash_id=0x09,
        kdf_sym_id=0x08,
    ),
    OpenPgpAlgoSpec(
        key="BrainpoolP512R1",
        label="Brainpool P512R1",
        sign_attrs=(0x13, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0D),
        decrypt_attrs=(0x12, 0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0D),
        aliases=("brainpoolp512r1", "brainpool p512r1"),
        kdf_hash_id=0x0A,
        kdf_sym_id=0x09,
    ),
    OpenPgpAlgoSpec(
        key="Secp256k1",
        label="secp256k1",
        sign_attrs=(0x13, 0x2B, 0x81, 0x04, 0x00, 0x0A),
        decrypt_attrs=(0x12, 0x2B, 0x81, 0x04, 0x00, 0x0A),
        aliases=("secp256k1",),
        kdf_hash_id=0x08,
        kdf_sym_id=0x07,
    ),
)

_OPENPGP_ALGO_BY_KEY = {spec.key: spec for spec in _OPENPGP_ALGO_SPECS}
_ENABLED_OPENPGP_ALGO_KEYS = frozenset({
    "Ed25519",
    "Cv25519",
    "P-256",
})
_OPENPGP_ALGO_CHOICES = {
    GpgKeySlot.SIGN: [
        ("Ed25519 (Recommended)", "Ed25519"),
        ("NIST P-256", "P-256"),
    ],
    GpgKeySlot.DECRYPT: [
        ("Cv25519 / X25519 (Recommended)", "Cv25519"),
        ("NIST P-256", "P-256"),
    ],
    GpgKeySlot.AUTH: [
        ("Ed25519 (Recommended)", "Ed25519"),
        ("NIST P-256", "P-256"),
    ],
}

# CRT tags for GENERATE ASYM KEY
_SLOT_CRT = {
    GpgKeySlot.SIGN:    [0xB6, 0x00],
    GpgKeySlot.DECRYPT: [0xB8, 0x00],
    GpgKeySlot.AUTH:    [0xA4, 0x00],
}

# Data Object tags for algo attributes PUT DATA
_SLOT_ALGO_TAG = {
    GpgKeySlot.SIGN:    0xC1,
    GpgKeySlot.DECRYPT: 0xC2,
    GpgKeySlot.AUTH:    0xC3,
}

# PUT DATA tags for per-slot fingerprint and creation timestamp
_SLOT_FP_TAG = {
    GpgKeySlot.SIGN:    0xC7,
    GpgKeySlot.DECRYPT: 0xC8,
    GpgKeySlot.AUTH:    0xC9,
}
_SLOT_TS_TAG = {
    GpgKeySlot.SIGN:    0xCE,
    GpgKeySlot.DECRYPT: 0xCF,
    GpgKeySlot.AUTH:    0xD0,
}

_SLOT_KEYREF = {
    GpgKeySlot.SIGN: "OPENPGP.1",
    GpgKeySlot.DECRYPT: "OPENPGP.2",
    GpgKeySlot.AUTH: "OPENPGP.3",
}


def _missing_gnupg_tool_message(tool: str) -> str:
    system = platform.system()
    if system == "Windows":
        return (
            f"{tool} is not installed or not in PATH.\n"
            "Install Gpg4win so `gpg`, `gpg-card`, and `gpgconf` are available."
        )
    if system == "Darwin":
        return (
            f"{tool} is not installed or not in PATH.\n"
            "Install GPG Suite or Homebrew GnuPG (`brew install gnupg`)."
        )
    return (
        f"{tool} is not installed or not in PATH.\n"
        "Install the `gnupg` package so `gpg`, `gpg-card`, and `gpgconf` are available."
    )


def _normalize_algo_alias(name: str) -> str:
    normalized = name.strip().lower().split(":", 1)[0]
    normalized = normalized.split("(", 1)[0].strip()
    if "/" in normalized:
        normalized = normalized.split("/", 1)[0].strip()
    return (
        normalized.replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
    )


def _find_openpgp_algo_spec(name: str) -> Optional[OpenPgpAlgoSpec]:
    normalized = _normalize_algo_alias(name)
    for spec in _OPENPGP_ALGO_SPECS:
        if normalized == _normalize_algo_alias(spec.key):
            return spec
        if normalized in {_normalize_algo_alias(alias) for alias in spec.aliases}:
            return spec
    return None


def _is_enabled_openpgp_algo(spec: OpenPgpAlgoSpec) -> bool:
    return spec.key in _ENABLED_OPENPGP_ALGO_KEYS


def supported_openpgp_algorithms(slot: GpgKeySlot) -> list[tuple[str, str]]:
    return list(_OPENPGP_ALGO_CHOICES.get(slot, []))


def supported_openpgp_algorithm_labels(slot: GpgKeySlot) -> list[str]:
    return [normalize_openpgp_algorithm_label(value) for _, value in supported_openpgp_algorithms(slot)]


def supported_openpgp_algorithm_summary(slot: GpgKeySlot) -> str:
    return ", ".join(supported_openpgp_algorithm_labels(slot))


def openpgp_candidate_matches_slot(slot: GpgKeySlot, algorithm_name: str) -> bool:
    spec = _find_openpgp_algo_spec(algorithm_name)
    if spec is None or not _is_enabled_openpgp_algo(spec):
        return False
    if slot == GpgKeySlot.DECRYPT:
        return spec.decrypt_attrs is not None
    return spec.sign_attrs is not None


def normalize_openpgp_algorithm_label(name: str) -> str:
    spec = _find_openpgp_algo_spec(name)
    return spec.label if spec is not None else name


def _strip_optional_pubkey_import_byte(attrs: List[int] | Tuple[int, ...]) -> Tuple[int, ...]:
    if len(attrs) > 2 and attrs[-1] == 0xFF:
        return tuple(attrs[:-1])
    return tuple(attrs)


def _sha256_digest_info(digest: bytes) -> bytes:
    """Return ASN.1 DigestInfo for SHA-256, used by RSA OpenPGP signatures."""
    return bytes.fromhex("3031300d060960864801650304020105000420") + digest


def _get_algo_attrs_for_slot(spec: OpenPgpAlgoSpec, slot: GpgKeySlot) -> Optional[Tuple[int, ...]]:
    if slot == GpgKeySlot.DECRYPT:
        return spec.decrypt_attrs
    return spec.sign_attrs


def _leading_zero_bits(byte: int) -> int:
    if byte == 0:
        return 8
    return 8 - byte.bit_length()


def _compute_v4_fingerprint(
    timestamp: int,
    algo_name: str,
    slot: GpgKeySlot,
    pubkey_raw: bytes,
) -> Optional[bytes]:
    """Compute OpenPGP v4 fingerprint (20-byte SHA-1) for an on-card generated key.

    Per RFC 4880 §12.2:
      fingerprint = SHA1(0x99 || uint16_be(body_len) || body)
      body        = 0x04 || uint32_be(timestamp) || algo_id || key_material

    key_material layout per RFC 4880 §5.6 / RFC 6637:
      For EdDSA  (algo 22): OID_len || OID || MPI(0x40 || raw_32)
      For ECDH   (algo 18): OID_len || OID || MPI(point) || KDF_params
      For ECDSA  (algo 19): OID_len || OID || MPI(point)

    pubkey_raw is what opcard returns in tag 0x86:
      - Ed25519 / Cv25519: raw 32 bytes (no prefix)
      - NIST P-256:        0x04 || x(32) || y(32) = 65 bytes
    """
    spec = _find_openpgp_algo_spec(algo_name)
    if spec is None:
        return None

    attrs = _get_algo_attrs_for_slot(spec, slot)
    if attrs is None:
        return None

    algo_id = 18 if slot == GpgKeySlot.DECRYPT else 19
    oid_bytes = bytes(attrs[1:])
    oid = bytes([len(oid_bytes)]) + oid_bytes

    if spec.is_25519:
        mpi_val = bytes([0x40]) + pubkey_raw
        bit_length = len(mpi_val) * 8 - _leading_zero_bits(mpi_val[0])
        key_material = oid + struct.pack(">H", bit_length) + mpi_val
        if slot == GpgKeySlot.SIGN or slot == GpgKeySlot.AUTH:
            algo_id = 22
    else:
        bit_length = len(pubkey_raw) * 8 - _leading_zero_bits(pubkey_raw[0])
        key_material = oid + struct.pack(">H", bit_length) + pubkey_raw

    if slot == GpgKeySlot.DECRYPT:
        if spec.kdf_hash_id is None or spec.kdf_sym_id is None:
            return None
        key_material += bytes([0x03, 0x01, spec.kdf_hash_id, spec.kdf_sym_id])

    body = bytes([0x04]) + struct.pack(">I", timestamp) + bytes([algo_id]) + key_material
    packet = bytes([0x99]) + struct.pack(">H", len(body)) + body
    return hashlib.sha1(packet).digest()


def _algo_name_from_attrs(attrs: List[int]) -> Optional[str]:
    """Derive a human-readable algorithm name from attribute bytes."""
    if not attrs:
        return None
    normalized = _strip_optional_pubkey_import_byte(attrs)
    for spec in _OPENPGP_ALGO_SPECS:
        if spec.sign_attrs == normalized or spec.decrypt_attrs == normalized:
            return spec.label
    algo_id = normalized[0]
    if algo_id == _ALGO_ID_RSA:
        return "RSA"
    return f"Unknown(0x{algo_id:02X})"


def _unix_to_iso(ts: int) -> Optional[str]:
    """Convert Unix timestamp to ISO date string. Returns None if ts == 0."""
    if ts == 0:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_ber_tlv(data: List[int]) -> Dict[int, List[int]]:
    """Very simple BER-TLV parser — handles 1- and 2-byte tags, 1-byte lengths.

    Returns a dict mapping integer tag → list of value bytes.
    Only parses the top level; does not recurse into constructed TLVs.
    """
    result: Dict[int, List[int]] = {}
    i = 0
    while i < len(data):
        if data[i] == 0x00 or data[i] == 0xFF:
            i += 1
            continue
        # Tag
        tag = data[i]
        i += 1
        if (tag & 0x1F) == 0x1F:
            # Two-byte tag
            if i >= len(data):
                break
            tag = (tag << 8) | data[i]
            i += 1
        if i >= len(data):
            break
        # Length
        length = data[i]
        i += 1
        if length == 0x81:
            if i >= len(data):
                break
            length = data[i]
            i += 1
        elif length == 0x82:
            if i + 1 >= len(data):
                break
            length = (data[i] << 8) | data[i + 1]
            i += 2
        elif length > 0x82:
            break
        value = data[i : i + length]
        i += length
        result[tag] = value
    return result


class GpgWorker(QObject):
    """Worker thread for OpenPGP card operations via PCSC."""

    gpg_probed = Signal(bool)                       # PCSC available + AID selectable
    status_loaded = Signal(list, dict)              # list[GpgKeyInfo], pw_status dict
    key_generated = Signal(bool, str, bytes, object, object)  # success, error, pubkey_bytes, slot, algo
    public_key_exported = Signal(bool, str, bytes, object, object)  # success, error, raw pubkey bytes, slot, algo
    keys_imported = Signal(bool, str, object)       # success, error, list[GpgKeySlot]
    text_signed = Signal(bool, str, str)            # success, message, base64 signature
    pin_changed = Signal(bool, str)                 # success, message
    reset_completed = Signal(bool, str)             # success, message
    error_occurred = Signal(str)

    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self._device = device
        self._connection = None
        self._reader = None

    def _pcsc_unavailable_message(self) -> str:
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
            return message
        return (
            "PCSC not available. Install pyscard and PCSC daemon:\n"
            "  sudo apt install pcscd pcsc-tools\n"
            "  pip install pyscard"
        )

    def _no_reader_message(self) -> str:
        if platform.system() == "Windows":
            return (
                "No PCSC readers found.\n"
                "The Smart Card service may be running, but Windows is not exposing a CCID reader for the device.\n\n"
                "Check:\n"
                "  - the SoloKeys CCID/smartcard interface is present in Device Manager\n"
                "  - the Smart Card service is running\n"
                "  - the correct smartcard/CCID driver is installed"
            )
        return (
            "No PCSC readers found.\n"
            "Make sure the device is connected and pcscd is running:\n"
            "  sudo systemctl start pcscd"
        )

    # ------------------------------------------------------------------
    # Internal PCSC helpers
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        """Open PCSC connection to a usable CCID reader.

        Solo 2 exposes an ICCD reader. Protocol selection (T=1 preferred,
        then auto) is handled transparently by iter_pcsc_connections().
        """
        if not PCSC_AVAILABLE:
            self.error_occurred.emit(self._pcsc_unavailable_message())
            return False

        for connection in iter_pcsc_connections():
            self._connection = connection
            self._reader = connection.reader_name
            return True

        self.error_occurred.emit(self._no_reader_message())
        return False

    def _disconnect(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None
        self._reader = None

    def _send_apdu(
        self,
        cla: int,
        ins: int,
        p1: int,
        p2: int,
        data: Optional[List[int]] = None,
        le: Optional[int] = None,
    ) -> Tuple[List[int], int, int]:
        """Send a raw APDU and return (response_data, sw1, sw2)."""
        apdu = [cla, ins, p1, p2]
        if data:
            apdu.append(len(data))
            apdu.extend(data)
        if le is not None:
            apdu.append(le)
        response, sw1, sw2 = self._connection.transmit(apdu)
        # Handle GET RESPONSE chaining (61 xx)
        while sw1 == 0x61:
            get_response = [0x00, 0xC0, 0x00, 0x00, sw2]
            more, sw1, sw2 = self._connection.transmit(get_response)
            response.extend(more)
        return response, sw1, sw2

    def _select_gpg_aid(self) -> bool:
        """SELECT the OpenPGP AID. Returns True on success (SW=9000)."""
        try:
            select_variants = (
                [0x00, INS_SELECT, 0x04, 0x00, len(GPG_AID)] + GPG_AID + [0x00],
                [0x00, INS_SELECT, 0x04, 0x00, len(GPG_AID)] + GPG_AID,
            )
            for apdu in select_variants:
                _, sw1, sw2 = self._connection.transmit(apdu)
                if (sw1 == 0x90 and sw2 == 0x00) or sw1 == 0x61:
                    return True
            return False
        except Exception:
            return False

    def _get_data(self, p1: int, p2: int) -> Optional[List[int]]:
        """GET DATA for the given P1/P2 tag. Returns value bytes or None."""
        try:
            resp, sw1, sw2 = self._send_apdu(0x00, INS_GET_DATA, p1, p2, le=0)
            if sw1 == 0x90 and sw2 == 0x00:
                return resp
            return None
        except Exception:
            return None

    def _sw_to_str(self, sw1: int, sw2: int) -> str:
        sw = (sw1 << 8) | sw2
        known = {
            0x9000: "OK",
            0x6700: "Wrong length",
            0x6982: "Security not satisfied",
            0x6983: "Authentication method blocked",
            0x6984: "Referenced data invalidated",
            0x6985: "Conditions of use not satisfied",
            0x6A80: "Incorrect parameters in data field",
            0x6A82: "File not found",
            0x6A86: "Incorrect P1/P2",
            0x6A88: "Referenced data not found",
            0x6D00: "Instruction code not supported",
            0x6E00: "Class not supported",
        }
        if sw in known:
            return f"SW={sw:04X} ({known[sw]})"
        if sw1 == 0x63:
            # Verification failed, retries in sw2 low nibble
            retries = sw2 & 0x0F
            return f"SW={sw:04X} (verification failed, {retries} retries left)"
        return f"SW={sw1:02X}{sw2:02X}"

    def _ensure_gpg_tool(self, tool: str) -> str:
        path = shutil.which(tool)
        if not path:
            raise RuntimeError(_missing_gnupg_tool_message(tool))
        return path

    def _run_cli(
        self,
        args: List[str],
        *,
        gnupghome: str,
        input_text: Optional[str] = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GNUPGHOME"] = gnupghome
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout).strip()
            raise RuntimeError(error_text or f"{args[0]} exited with status {result.returncode}")
        return result

    def _kill_temp_agent(self, gnupghome: str) -> None:
        gpgconf = shutil.which("gpgconf")
        if not gpgconf:
            return
        env = os.environ.copy()
        env["GNUPGHOME"] = gnupghome
        for component in ("scdaemon", "gpg-agent"):
            try:
                subprocess.run(
                    [gpgconf, "--kill", component],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                pass

    def _prepare_temp_gnupg_home(self, gnupghome: str) -> None:
        os.chmod(gnupghome, 0o700)
        with open(os.path.join(gnupghome, "scdaemon.conf"), "w", encoding="utf-8") as handle:
            handle.write("disable-ccid\n")

    def _import_secret_key_export(
        self,
        gnupghome: str,
        export_path: str,
        passphrase: Optional[str],
    ) -> None:
        self._ensure_gpg_tool("gpg")
        args = ["gpg", "--batch", "--yes"]
        if passphrase:
            args.extend(["--pinentry-mode", "loopback", "--passphrase", passphrase])
        args.extend(["--import", export_path])
        self._run_cli(args, gnupghome=gnupghome)

    def _list_secret_key_candidates(self, gnupghome: str) -> List[GpgImportCandidate]:
        result = self._run_cli(
            ["gpg", "--batch", "--with-colons", "--with-keygrip", "--list-secret-keys"],
            gnupghome=gnupghome,
        )
        candidates: List[GpgImportCandidate] = []
        current: Optional[GpgImportCandidate] = None
        current_user_id = ""
        for line in result.stdout.splitlines():
            if not line:
                continue
            fields = line.split(":")
            record_type = fields[0]
            if record_type == "sec":
                current = GpgImportCandidate(
                    keygrip="",
                    fingerprint="",
                    keyid=fields[4],
                    user_id="",
                    algorithm=self._format_import_algorithm(fields[3], fields[16] if len(fields) > 16 else ""),
                    capabilities=fields[11],
                    is_primary=True,
                    created=_unix_to_iso(int(fields[5])) if fields[5] else None,
                )
                candidates.append(current)
                current_user_id = ""
            elif record_type == "uid" and candidates:
                user_id = fields[9] if len(fields) > 9 else ""
                if candidates[-1].is_primary and not candidates[-1].user_id:
                    candidates[-1].user_id = user_id
                    current_user_id = user_id
            elif record_type == "ssb":
                current = GpgImportCandidate(
                    keygrip="",
                    fingerprint="",
                    keyid=fields[4],
                    user_id=current_user_id,
                    algorithm=self._format_import_algorithm(fields[3], fields[16] if len(fields) > 16 else ""),
                    capabilities=fields[11],
                    is_primary=False,
                    created=_unix_to_iso(int(fields[5])) if fields[5] else None,
                )
                candidates.append(current)
            elif record_type == "fpr" and current is not None and len(fields) > 9:
                current.fingerprint = fields[9]
            elif record_type == "grp" and current is not None and len(fields) > 9:
                current.keygrip = fields[9]
        return [candidate for candidate in candidates if candidate.fingerprint and candidate.keygrip]

    def _warm_secret_key_cache(
        self,
        gnupghome: str,
        fingerprints: List[str],
        passphrase: Optional[str],
    ) -> None:
        if not passphrase:
            return
        for fingerprint in sorted(set(fingerprints)):
            self._run_cli(
                [
                    "gpg",
                    "--batch",
                    "--yes",
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase",
                    passphrase,
                    "--armor",
                    "--export-secret-keys",
                    fingerprint,
                ],
                gnupghome=gnupghome,
            )

    def _format_import_algorithm(self, algo_id: str, curve_name: str) -> str:
        normalized_curve = curve_name.strip()
        if normalized_curve:
            spec = _find_openpgp_algo_spec(normalized_curve)
            if spec is not None:
                return spec.label
            return normalized_curve
        if curve_name:
            return curve_name
        return {
            "18": "ECDH",
            "19": "ECDSA",
            "22": "EdDSA",
            "1": "RSA",
        }.get(algo_id, f"Algo {algo_id}")

    # ------------------------------------------------------------------
    # Public worker slots
    # ------------------------------------------------------------------

    def probe_gpg(self) -> None:
        """Check whether the OpenPGP AID is selectable on any PCSC reader.

        Retries up to 5 times with 500 ms delay: the CCID interface comes up
        later than HID after a device plug-in event.
        """
        if not PCSC_AVAILABLE:
            self.gpg_probed.emit(False)
            return
        for attempt in range(6):
            if attempt > 0:
                time.sleep(0.5)
            try:
                if self._connect():
                    ok = self._select_gpg_aid()
                    self._disconnect()
                    if ok:
                        self.gpg_probed.emit(True)
                        return
                    # AID not found — no point retrying
                    self.gpg_probed.emit(False)
                    return
            except Exception:
                self._disconnect()
        self.gpg_probed.emit(False)

    def load_status(self) -> None:
        """Load key slot info and PW status, emit status_loaded."""
        if not PCSC_AVAILABLE:
            self.error_occurred.emit("PCSC not available")
            return
        try:
            if not self._connect():
                self.error_occurred.emit("Cannot connect to card reader")
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.error_occurred.emit("OpenPGP applet not found")
                return

            key_infos = self._read_key_infos()
            pw_status = self._read_pw_status()
            self._disconnect()
            self.status_loaded.emit(key_infos, pw_status)
        except Exception as e:
            self._disconnect()
            self.error_occurred.emit(str(e))

    def inspect_import_file(
        self,
        export_path: str,
        passphrase: Optional[str] = None,
    ) -> List[GpgImportCandidate]:
        with tempfile.TemporaryDirectory(prefix="solo2-gpg-import-") as gnupghome:
            try:
                self._prepare_temp_gnupg_home(gnupghome)
                self._import_secret_key_export(gnupghome, export_path, passphrase)
                return self._list_secret_key_candidates(gnupghome)
            finally:
                self._kill_temp_agent(gnupghome)

    def import_keys_from_export(
        self,
        export_path: str,
        slot_mapping: Dict[GpgKeySlot, str],
        passphrase: Optional[str] = None,
    ) -> None:
        if not PCSC_AVAILABLE:
            self.keys_imported.emit(False, "PCSC not available", [])
            return
        try:
            self._ensure_gpg_tool("gpg")
            self._ensure_gpg_tool("gpg-card")
            if not slot_mapping:
                raise RuntimeError("No target slots selected for import")

            ordered_slots = [slot for slot in (GpgKeySlot.SIGN, GpgKeySlot.DECRYPT, GpgKeySlot.AUTH) if slot in slot_mapping]

            with tempfile.TemporaryDirectory(prefix="solo2-gpg-import-") as gnupghome:
                try:
                    self._prepare_temp_gnupg_home(gnupghome)
                    self._import_secret_key_export(gnupghome, export_path, passphrase)
                    candidates = {
                        candidate.keygrip: candidate
                        for candidate in self._list_secret_key_candidates(gnupghome)
                    }
                    for slot in ordered_slots:
                        if slot_mapping[slot] not in candidates:
                            raise RuntimeError(f"Selected key for {slot.value} slot is not available in the imported keyring")
                        candidate = candidates[slot_mapping[slot]]
                        if not openpgp_candidate_matches_slot(slot, candidate.algorithm):
                            raise RuntimeError(
                                f"{normalize_openpgp_algorithm_label(candidate.algorithm)} is not supported for the {slot.value} slot "
                                f"on this firmware. Supported algorithms: {supported_openpgp_algorithm_summary(slot)}."
                            )
                    self._warm_secret_key_cache(
                        gnupghome,
                        [candidates[slot_mapping[slot]].fingerprint for slot in ordered_slots],
                        passphrase,
                    )

                    command = ["gpg-card", "--no-history"]
                    for index, slot in enumerate(ordered_slots):
                        if index:
                            command.append("--")
                        command.extend(
                            [
                                "writekey",
                                "--force",
                                _SLOT_KEYREF[slot],
                                slot_mapping[slot],
                            ]
                        )
                    self._run_cli(command, gnupghome=gnupghome)
                finally:
                    self._kill_temp_agent(gnupghome)

            self.keys_imported.emit(True, "", ordered_slots)
        except Exception as exc:
            self.keys_imported.emit(False, str(exc), [])

    def export_public_key(self, slot: GpgKeySlot) -> None:
        """Read the current public key for the given slot from the card."""
        if not PCSC_AVAILABLE:
            self.public_key_exported.emit(False, "PCSC not available", b"", slot, None)
            return
        try:
            if not self._connect():
                self.public_key_exported.emit(False, "Cannot connect to card reader", b"", slot, None)
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.public_key_exported.emit(False, "OpenPGP applet not found", b"", slot, None)
                return

            algo = None
            for info in self._read_key_infos():
                if info.slot == slot:
                    algo = info.algo
                    break

            crt = _SLOT_CRT[slot]
            resp, sw1, sw2 = self._send_apdu(
                0x00, INS_GENERATE_ASYM_KEY, 0x81, 0x00, crt, 0
            )
            self._disconnect()
            if not (sw1 == 0x90 and sw2 == 0x00):
                self.public_key_exported.emit(
                    False,
                    f"Public key export failed: {self._sw_to_str(sw1, sw2)}",
                    b"",
                    slot,
                    algo,
                )
                return

            pubkey_raw = self._parse_pubkey_from_response(resp)
            if pubkey_raw is None:
                self.public_key_exported.emit(
                    False,
                    "Public key export failed: could not parse card response",
                    b"",
                    slot,
                    algo,
                )
                return

            self.public_key_exported.emit(True, "", pubkey_raw, slot, algo)
        except Exception as exc:
            self._disconnect()
            self.public_key_exported.emit(False, str(exc), b"", slot, None)

    def generate_key(self, slot: GpgKeySlot, algo_name: str, admin_pin: str) -> None:
        """Generate a key in the given slot.

        algo_name: canonical OpenPGP algorithm key for this slot
        admin_pin: the Admin PIN (PW3), required for key generation.
        """
        if not PCSC_AVAILABLE:
            self.key_generated.emit(False, "PCSC not available", b"", slot, algo_name)
            return
        try:
            if not self._connect():
                self.key_generated.emit(False, "Cannot connect to card reader", b"", slot, algo_name)
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.key_generated.emit(False, "OpenPGP applet not found", b"", slot, algo_name)
                return

            spec = _find_openpgp_algo_spec(algo_name)
            attrs = _get_algo_attrs_for_slot(spec, slot) if spec is not None else None
            if spec is None or attrs is None or not _is_enabled_openpgp_algo(spec):
                self._disconnect()
                self.key_generated.emit(
                    False,
                    f"Unsupported algorithm for this firmware build: {algo_name}",
                    b"",
                    slot,
                    algo_name,
                )
                return

            # VERIFY PW3 (Admin PIN) — must happen before any PUT DATA
            pin_bytes = list(admin_pin.encode("utf-8"))
            _, sw1, sw2 = self._send_apdu(0x00, INS_VERIFY, 0x00, 0x83, pin_bytes)
            if not (sw1 == 0x90 and sw2 == 0x00):
                self._disconnect()
                self.key_generated.emit(
                    False, f"Admin PIN rejected: {self._sw_to_str(sw1, sw2)}", b"", slot, algo_name
                )
                return

            # PUT DATA: set algorithm attributes
            tag = _SLOT_ALGO_TAG[slot]
            _, sw1, sw2 = self._send_apdu(0x00, INS_PUT_DATA, 0x00, tag, list(attrs))
            if not (sw1 == 0x90 and sw2 == 0x00):
                self._disconnect()
                self.key_generated.emit(
                    False,
                    f"Failed to set algo attrs: {self._sw_to_str(sw1, sw2)}",
                    b"",
                    slot,
                    algo_name,
                )
                return

            # GENERATE ASYM KEY
            crt = _SLOT_CRT[slot]
            resp, sw1, sw2 = self._send_apdu(
                0x00, INS_GENERATE_ASYM_KEY, 0x80, 0x00, crt, 0
            )
            if not (sw1 == 0x90 and sw2 == 0x00):
                self._disconnect()
                self.key_generated.emit(
                    False,
                    f"Key generation failed: {self._sw_to_str(sw1, sw2)}",
                    b"",
                    slot,
                    algo_name,
                )
                return

            # Write creation timestamp and fingerprint so the key is GPG-compatible.
            # These are not set by the card itself during key generation.
            ts = int(time.time())
            pubkey_raw = self._parse_pubkey_from_response(resp)
            pubkey_bytes = pubkey_raw if pubkey_raw is not None else bytes(resp)
            if pubkey_raw is not None:
                fp = _compute_v4_fingerprint(ts, algo_name, slot, pubkey_raw)
                if fp is not None:
                    # Timestamp (4-byte big-endian Unix time)
                    ts_data = list(struct.pack(">I", ts))
                    self._send_apdu(0x00, INS_PUT_DATA, 0x00, _SLOT_TS_TAG[slot], ts_data)
                    # Fingerprint (20 bytes)
                    self._send_apdu(0x00, INS_PUT_DATA, 0x00, _SLOT_FP_TAG[slot], list(fp))

            self._disconnect()
            self.key_generated.emit(True, "", pubkey_bytes, slot, spec.label)

        except Exception as e:
            self._disconnect()
            self.key_generated.emit(False, str(e), b"", slot, algo_name)

    def sign_text(self, text: str, user_pin: str) -> None:
        """Sign UTF-8 text with the OpenPGP SIG key after verifying PW1."""
        if not PCSC_AVAILABLE:
            self.text_signed.emit(False, "PCSC not available", "")
            return
        try:
            if not self._connect():
                self.text_signed.emit(False, "Cannot connect to card reader", "")
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.text_signed.emit(False, "OpenPGP applet not found", "")
                return

            sign_info = next(
                (info for info in self._read_key_infos() if info.slot == GpgKeySlot.SIGN),
                None,
            )
            if sign_info is None or not sign_info.has_key:
                self._disconnect()
                self.text_signed.emit(False, "No OpenPGP signing key is present", "")
                return

            pin_bytes = list(user_pin.encode("utf-8"))
            _, sw1, sw2 = self._send_apdu(0x00, INS_VERIFY, 0x00, 0x81, pin_bytes)
            if not (sw1 == 0x90 and sw2 == 0x00):
                self._disconnect()
                self.text_signed.emit(
                    False, f"User PIN rejected: {self._sw_to_str(sw1, sw2)}", ""
                )
                return

            digest = hashlib.sha256(text.encode("utf-8")).digest()
            data = list(_sha256_digest_info(digest)) if sign_info.algo == "RSA" else list(digest)
            signature, sw1, sw2 = self._send_apdu(0x00, INS_PSO, 0x9E, 0x9A, data, 0)
            self._disconnect()
            if sw1 == 0x90 and sw2 == 0x00:
                self.text_signed.emit(
                    True,
                    f"Signed SHA-256 digest with {sign_info.algo or 'the OpenPGP SIG key'}",
                    base64.b64encode(bytes(signature)).decode("ascii"),
                )
            else:
                self.text_signed.emit(False, f"Signing failed: {self._sw_to_str(sw1, sw2)}", "")
        except Exception as e:
            self._disconnect()
            self.text_signed.emit(False, str(e), "")

    def change_user_pin(self, old_pin: str, new_pin: str) -> None:
        """Change User PIN (PW1, P2=81)."""
        self._change_pin(0x81, old_pin, new_pin)

    def change_admin_pin(self, old_pin: str, new_pin: str) -> None:
        """Change Admin PIN (PW3, P2=83)."""
        self._change_pin(0x83, old_pin, new_pin)

    def _change_pin(self, p2: int, old_pin: str, new_pin: str) -> None:
        if not PCSC_AVAILABLE:
            self.pin_changed.emit(False, "PCSC not available")
            return
        try:
            if not self._connect():
                self.pin_changed.emit(False, "Cannot connect to card reader")
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.pin_changed.emit(False, "OpenPGP applet not found")
                return

            old_bytes = list(old_pin.encode("utf-8"))
            new_bytes = list(new_pin.encode("utf-8"))
            data = old_bytes + new_bytes
            _, sw1, sw2 = self._send_apdu(0x00, INS_CHANGE_REFERENCE_DATA, 0x00, p2, data)
            self._disconnect()
            if sw1 == 0x90 and sw2 == 0x00:
                label = "User PIN" if p2 == 0x81 else "Admin PIN"
                self.pin_changed.emit(True, f"{label} changed successfully")
            else:
                self.pin_changed.emit(False, f"PIN change failed: {self._sw_to_str(sw1, sw2)}")
        except Exception as e:
            self._disconnect()
            self.pin_changed.emit(False, str(e))

    def factory_reset(self) -> None:
        """Factory-reset the OpenPGP applet (TERMINATE DF + ACTIVATE FILE)."""
        if not PCSC_AVAILABLE:
            self.reset_completed.emit(False, "PCSC not available")
            return
        try:
            if not self._connect():
                self.reset_completed.emit(False, "Cannot connect to card reader")
                return
            if not self._select_gpg_aid():
                self._disconnect()
                self.reset_completed.emit(False, "OpenPGP applet not found")
                return

            # TERMINATE DF
            _, sw1, sw2 = self._send_apdu(0x00, INS_TERMINATE_DF, 0x00, 0x00)
            if not (sw1 == 0x90 and sw2 == 0x00):
                self._disconnect()
                self.reset_completed.emit(
                    False, f"TERMINATE DF failed: {self._sw_to_str(sw1, sw2)}"
                )
                return

            # ACTIVATE FILE
            _, sw1, sw2 = self._send_apdu(0x00, INS_ACTIVATE_FILE, 0x00, 0x00)
            self._disconnect()
            if sw1 == 0x90 and sw2 == 0x00:
                self.reset_completed.emit(True, "OpenPGP applet reset to factory defaults")
            else:
                self.reset_completed.emit(
                    False, f"ACTIVATE FILE failed: {self._sw_to_str(sw1, sw2)}"
                )
        except Exception as e:
            self._disconnect()
            self.reset_completed.emit(False, str(e))

    # ------------------------------------------------------------------
    # Internal status parsing
    # ------------------------------------------------------------------

    def _parse_pubkey_from_response(self, response: List[int]) -> Optional[bytes]:
        """Extract raw public key bytes from a GENERATE ASYM KEY response.

        Response structure: 7F49 [len] 86 [len] [key_bytes]
        Returns the value of tag 0x86 as bytes, or None if not found.
        """
        outer = _parse_ber_tlv(response)
        inner_bytes = outer.get(0x7F49)
        if inner_bytes is not None:
            inner = _parse_ber_tlv(inner_bytes)
            val = inner.get(0x86)
            if val is not None:
                return bytes(val)
        # Fallback: tag 0x86 at top level
        val = outer.get(0x86)
        return bytes(val) if val is not None else None

    def _slot_has_key(self, slot: GpgKeySlot) -> bool:
        """Return True if the slot contains a key, using GENERATE ASYM KEY P1=0x81 (read mode).

        opcard never writes fingerprint (C5) or timestamp (CD) on its own — those
        are set by gpg after computing them from the public key.  The only reliable
        check is to ask the card to read back the stored public key: SW=9000 means
        a key is present; anything else (6A88 = KeyReferenceNotFound, etc.) means empty.
        """
        try:
            crt = _SLOT_CRT[slot]
            _, sw1, sw2 = self._send_apdu(0x00, INS_GENERATE_ASYM_KEY, 0x81, 0x00, crt, 0)
            return sw1 == 0x90 and sw2 == 0x00
        except Exception:
            return False

    def _read_key_infos(self) -> List[GpgKeyInfo]:
        """Read key slot info: presence via read-key probe, algo attrs from GET DATA (6E)."""
        # GET DATA 00 CA 00 6E → Application Related Data.
        # Structure: 6E → { 4F(AID), 5F52(hist), 73(Discretionary) → { C0,C1,C2,C3,C4,C5,CD } }
        # C1/C2/C3/C5/CD live inside tag 73, so we must descend into it.
        raw = self._get_data(0x00, 0x6E)
        tlv: Dict[int, List[int]] = {}
        if raw:
            # Peel off outer 6E wrapper if present
            level0 = _parse_ber_tlv(raw)
            level1_bytes = level0.get(0x6E, raw)
            level1 = _parse_ber_tlv(level1_bytes)
            # Descend into tag 73 (Discretionary data objects)
            disc = level1.get(0x73)
            tlv = _parse_ber_tlv(disc) if disc else level1

        # Fingerprints: tag C5, 60 bytes = 3 × 20.
        # Only set when gpg writes them back after computing from the public key.
        fps: List[Optional[str]] = [None, None, None]
        fp_raw = tlv.get(0xC5)
        if fp_raw and len(fp_raw) >= 60:
            for i in range(3):
                chunk = fp_raw[i * 20 : (i + 1) * 20]
                if any(b != 0 for b in chunk):
                    fps[i] = "".join(f"{b:02x}" for b in chunk)

        # Creation timestamps: tag CD, 12 bytes = 3 × 4 (big-endian u32).
        # Also only set by gpg, not by the card during key generation.
        ts_list: List[int] = [0, 0, 0]
        cd_raw = tlv.get(0xCD)
        if cd_raw and len(cd_raw) >= 12:
            for i in range(3):
                ts_list[i] = struct.unpack(">I", bytes(cd_raw[i * 4 : (i + 1) * 4]))[0]

        # Algo attributes: tags C1, C2, C3 — set by our PUT DATA before key generation.
        algo_tags = [tlv.get(0xC1), tlv.get(0xC2), tlv.get(0xC3)]

        slots_order = [GpgKeySlot.SIGN, GpgKeySlot.DECRYPT, GpgKeySlot.AUTH]
        infos: List[GpgKeyInfo] = []
        for idx, slot in enumerate(slots_order):
            has_key = self._slot_has_key(slot)
            fp = fps[idx]
            ts = ts_list[idx]
            attrs = algo_tags[idx]
            infos.append(GpgKeyInfo(
                slot=slot,
                has_key=has_key,
                fingerprint=fp,
                algo=_algo_name_from_attrs(attrs) if (has_key and attrs) else None,
                created=_unix_to_iso(ts),
            ))
        return infos

    def _read_pw_status(self) -> Dict:
        """Parse PW Status Bytes (tag 00C4)."""
        raw = self._get_data(0x00, 0xC4)
        status = {
            "user_pin_retries": None,
            "reset_code_retries": None,
            "admin_pin_retries": None,
        }
        if raw and len(raw) >= 7:
            # Byte 4: retries for PW1 (user), byte 5: reset code, byte 6: PW3 (admin)
            status["user_pin_retries"] = raw[4]
            status["reset_code_retries"] = raw[5]
            status["admin_pin_retries"] = raw[6]
        return status
