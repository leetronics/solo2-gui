"""Provision app worker for SoloKeys GUI (Hacker variant only).

Communicates with the Solo 2 Provision application via PCSC.
AID: A0 00 00 08 47 01 00 00 01

Requires pyscard + pcscd (same as PIV).
"""

from typing import Optional, List

from PySide6.QtCore import QObject, Signal

try:
    from smartcard.System import readers
    from smartcard.Exceptions import NoCardException, CardConnectionException

    PCSC_AVAILABLE = True
except ImportError:
    PCSC_AVAILABLE = False

# Provision app AID
PROVISION_AID = [0xA0, 0x00, 0x00, 0x08, 0x47, 0x01, 0x00, 0x00, 0x01]

# Instruction bytes
INS_SELECT = 0xA4
INS_GEN_ED25519 = 0xBB
INS_GEN_P256 = 0xBC
INS_GEN_X25519 = 0xB7
INS_STORE_ED25519_CERT = 0xB9
INS_STORE_P256_CERT = 0xBA
INS_STORE_X25519_CERT = 0xB6
INS_STORE_T1_PUBKEY = 0xB5
INS_REFORMAT_FS = 0xBD
INS_WRITE_FILE = 0xBF

KEY_TYPES = {
    "ed25519": (INS_GEN_ED25519, INS_STORE_ED25519_CERT, 32),
    "p256": (INS_GEN_P256, INS_STORE_P256_CERT, 64),
    "x25519": (INS_GEN_X25519, INS_STORE_X25519_CERT, 32),
}


class ProvisionWorker(QObject):
    """Worker for Solo 2 Provision app operations (Hacker variant only)."""

    operation_completed = Signal(bool, str)      # success, message
    keypair_generated = Signal(str, bytes)       # key_type, pubkey_bytes
    error_occurred = Signal(str)

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._connection = None

    def _connect_pcsc(self) -> bool:
        """Connect to the Provision applet.

        Tries all available PCSC readers and uses the first one that
        responds successfully to Provision AID SELECT.
        """
        if not PCSC_AVAILABLE:
            self.error_occurred.emit(
                "PCSC not available. Install pyscard and PCSC daemon:\n"
                "  sudo apt install pcscd pcsc-tools\n"
                "  pip install pyscard"
            )
            return False

        try:
            reader_list = readers()
        except Exception as e:
            self.error_occurred.emit(f"Failed to list PCSC readers: {e}")
            return False

        if not reader_list:
            self.error_occurred.emit(
                "No PCSC readers found. Make sure pcscd is running."
            )
            return False

        select_variants = [
            [0x00, INS_SELECT, 0x04, 0x00, len(PROVISION_AID)] + PROVISION_AID,         # Case 3
            [0x00, INS_SELECT, 0x04, 0x00, len(PROVISION_AID)] + PROVISION_AID + [0x00], # Case 4
        ]
        last_error = "No reader responded to Provision applet SELECT"

        for reader in reader_list:
            try:
                conn = reader.createConnection()
                conn.connect()
                for select_cmd in select_variants:
                    response, sw1, sw2 = conn.transmit(select_cmd)
                    if sw1 == 0x90 and sw2 == 0x00:
                        self._connection = conn
                        return True
                    last_error = (
                        f"Provision SELECT failed on '{reader}': SW={sw1:02X}{sw2:02X}\n"
                        "This feature requires a Solo 2 Hacker device."
                    )
                try:
                    conn.disconnect()
                except Exception:
                    pass
            except NoCardException:
                last_error = f"No card in '{reader}'"
            except CardConnectionException as e:
                last_error = f"Connection failed on '{reader}': {e}"
            except Exception as e:
                last_error = f"Error on '{reader}': {e}"

        self.error_occurred.emit(f"Provision applet not found. {last_error}")
        return False

    def _disconnect(self) -> None:
        if self._connection:
            try:
                self._connection.disconnect()
            except Exception:
                pass
            self._connection = None

    def _send_apdu(
        self, ins: int, p1: int = 0, p2: int = 0, data: bytes = b""
    ) -> Optional[bytes]:
        """Send APDU and return response bytes on SW=9000, None otherwise."""
        if not self._connection:
            return None

        apdu = [0x00, ins, p1, p2]
        if data:
            apdu.append(len(data))
            apdu.extend(data)
        apdu.append(0x00)  # Le

        response, sw1, sw2 = self._connection.transmit(apdu)
        if sw1 == 0x90 and sw2 == 0x00:
            return bytes(response)
        return None

    def generate_key(self, key_type: str) -> None:
        """Generate an attestation keypair on the device.

        Emits keypair_generated(key_type, pubkey_bytes) on success.
        """
        info = KEY_TYPES.get(key_type)
        if not info:
            self.error_occurred.emit(f"Unknown key type: {key_type}")
            return

        gen_ins, _, expected_len = info

        if not self._connect_pcsc():
            return

        try:
            result = self._send_apdu(gen_ins)
            if result is None:
                self.error_occurred.emit(f"Key generation failed for {key_type}")
                return

            pubkey = result[:expected_len]
            self.keypair_generated.emit(key_type, pubkey)
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            self._disconnect()

    def store_certificate(self, key_type: str, der_data: bytes) -> None:
        """Store an attestation certificate for the given key type."""
        info = KEY_TYPES.get(key_type)
        if not info:
            self.error_occurred.emit(f"Unknown key type: {key_type}")
            return

        _, store_ins, _ = info

        if not self._connect_pcsc():
            return

        try:
            result = self._send_apdu(store_ins, data=der_data)
            if result is None:
                self.operation_completed.emit(False, f"Failed to store {key_type} certificate")
            else:
                self.operation_completed.emit(True, f"{key_type} certificate stored successfully")
        except Exception as e:
            self.operation_completed.emit(False, str(e))
        finally:
            self._disconnect()

    def store_t1_pubkey(self, pubkey_bytes: bytes) -> None:
        """Store the T1 intermediate public key (32-byte Ed25519)."""
        if len(pubkey_bytes) != 32:
            self.operation_completed.emit(False, "T1 public key must be exactly 32 bytes")
            return

        if not self._connect_pcsc():
            return

        try:
            result = self._send_apdu(INS_STORE_T1_PUBKEY, data=pubkey_bytes)
            if result is None:
                self.operation_completed.emit(False, "Failed to store T1 public key")
            else:
                self.operation_completed.emit(True, "T1 public key stored successfully")
        except Exception as e:
            self.operation_completed.emit(False, str(e))
        finally:
            self._disconnect()

    def reformat_filesystem(self) -> None:
        """Reformat the device filesystem. Destructive — all data is erased."""
        if not self._connect_pcsc():
            return

        try:
            result = self._send_apdu(INS_REFORMAT_FS)
            if result is None:
                self.operation_completed.emit(False, "Filesystem reformat failed")
            else:
                self.operation_completed.emit(True, "Filesystem reformatted successfully")
        except Exception as e:
            self.operation_completed.emit(False, str(e))
        finally:
            self._disconnect()

    def write_file(self, path: str, data: bytes) -> None:
        """Write a file to the device filesystem."""
        path_bytes = path.encode("utf-8")
        if len(path_bytes) > 255:
            self.operation_completed.emit(False, "File path too long")
            return

        payload = bytes([len(path_bytes)]) + path_bytes + data

        if not self._connect_pcsc():
            return

        try:
            result = self._send_apdu(INS_WRITE_FILE, data=payload)
            if result is None:
                self.operation_completed.emit(False, f"Failed to write file: {path}")
            else:
                self.operation_completed.emit(True, f"File written: {path}")
        except Exception as e:
            self.operation_completed.emit(False, str(e))
        finally:
            self._disconnect()
