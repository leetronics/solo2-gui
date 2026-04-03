"""OATH APDU bridge — pure Python OATH protocol over DeviceManager.send_browser_apdu()."""

import base64
import re
import struct
import threading
import time

PASSWORD_ONLY_PREFIX = "__solo_pw__:"


def strip_password_only_label(name: str) -> str:
    if name.startswith(PASSWORD_ONLY_PREFIX):
        return name[len(PASSWORD_ONLY_PREFIX):]
    return name


def is_password_only_label(name: str) -> bool:
    return name.startswith(PASSWORD_ONLY_PREFIX)


def encode_password_only_label(name: str) -> str:
    return f"{PASSWORD_ONLY_PREFIX}{name}"


# APDU instruction bytes
INS_PUT = 0x01
INS_DELETE = 0x02
INS_LIST = 0xA1
INS_CALCULATE = 0xA2
INS_SEND_REMAINING = 0xA5
INS_VERIFY_PIN = 0xB2
INS_CHANGE_PIN = 0xB3
INS_SET_PIN = 0xB4
INS_GET_CREDENTIAL = 0xB5
INS_UPDATE_CREDENTIAL = 0xB7

# TLV tags
TAG_NAME = 0x71
TAG_NAME_LIST = 0x72
TAG_KEY = 0x73
TAG_CHALLENGE = 0x74
TAG_RESPONSE = 0x76
TAG_PROPERTY = 0x78
TAG_PASSWORD = 0x80
TAG_NEW_PASSWORD = 0x81
TAG_PWS_LOGIN = 0x83
TAG_PWS_PASSWORD = 0x84
TAG_PWS_METADATA = 0x85

# Credential properties
# Note: These differ from the standard OATH spec - firmware uses compact 2-byte encoding
# Bits: 0x02 = RequireTouch, 0x04 = PINEncrypt
PROP_TOUCH_REQUIRED = 0x02
PROP_PIN_ENCRYPTED = 0x04

# Kind/algo nibbles (high nibble = type, low nibble = algorithm)
KIND_TOTP = 0x20
KIND_HOTP = 0x10
KIND_REVERSE_HOTP = 0x30
KIND_HMAC = 0x40
ALGO_SHA1 = 0x01
ALGO_SHA256 = 0x02
ALGO_SHA512 = 0x03

# Status words
SW_SUCCESS = 0x9000
SW_MORE_DATA_MASK = 0x6100  # 0x61xx
SW_TOUCH_REQUIRED = 0x6985
SW_PIN_REQUIRED = 0x6982
SW_WRONG_PIN_MASK = 0x63C0  # 0x63Cx
SW_PIN_BLOCKED = 0x6983


class OATHError(Exception):
    pass


class OATHTouchRequired(OATHError):
    pass


class OATHPINRequired(OATHError):
    pass


class OATHBridge:
    """High-level OATH operations.  All methods block the calling thread.

    ``transport`` is an optional callable ``(apdu: bytes) -> bytes``.  When
    provided it is called synchronously (native-host direct-HID path).  When
    omitted the existing DeviceManager async path is used (GUI path).
    """

    def __init__(self, transport=None):
        self._transport = transport

    # ------------------------------------------------------------------
    # TLV helpers

    @staticmethod
    def _build_tlv(tag: int, value: bytes) -> bytes:
        return bytes([tag, len(value)]) + value

    @staticmethod
    def _parse_tlv(data: bytes) -> list:
        """Return list of (tag, value) tuples."""
        items = []
        offset = 0
        while offset + 2 <= len(data):
            tag = data[offset]
            length = data[offset + 1]
            value = data[offset + 2: offset + 2 + length]
            items.append((tag, value))
            offset += 2 + length
        return items

    # ------------------------------------------------------------------
    # Low-level transport

    def _raw_send(self, apdu: bytes) -> bytes:
        """Send raw APDU bytes and return raw response bytes.

        If a transport callable was provided at construction time it is called
        directly (synchronous, native-host path).  Otherwise the DeviceManager
        async path is used (GUI path).
        """
        if self._transport is not None:
            try:
                return self._transport(apdu)
            except Exception as e:
                raise OATHError(str(e))

        from solo_gui.device_manager import DeviceManager

        dm = DeviceManager.get_instance()
        if not dm._running:
            raise OATHError("No SoloKeys device connected")

        result_holder: list = [None]
        error_holder: list = [None]
        done = threading.Event()

        def callback(result, error):
            result_holder[0] = result
            error_holder[0] = error
            done.set()

        dm.send_browser_apdu(apdu, callback)
        done.wait(timeout=10)

        if error_holder[0]:
            raise OATHError(error_holder[0])
        if result_holder[0] is None:
            raise OATHError("Timeout waiting for device")

        return bytes(result_holder[0])

    @staticmethod
    def _build_apdu(ins: int, p1: int = 0, p2: int = 0, data: bytes = b'') -> bytes:
        apdu = bytes([0x00, ins, p1, p2])
        if data:
            if len(data) <= 255:
                apdu += bytes([len(data)]) + data
            else:
                apdu += bytes([0x00, (len(data) >> 8) & 0xFF, len(data) & 0xFF]) + data
        return apdu

    def _interpret_sw(self, sw: int) -> None:
        """Raise the appropriate exception for bad status words."""
        if sw == SW_TOUCH_REQUIRED:
            raise OATHTouchRequired("Touch required")
        if sw == SW_PIN_REQUIRED:
            raise OATHPINRequired("PIN required")
        if (sw & 0xFFF0) == SW_WRONG_PIN_MASK:
            attempts = sw & 0x0F
            raise OATHError(f"Wrong PIN ({attempts} retries left)")
        if sw == SW_PIN_BLOCKED:
            raise OATHError("PIN blocked")
        raise OATHError(f"APDU error: 0x{sw:04X}")

    @staticmethod
    def _name_candidates(name: str) -> list[str]:
        """Return plausible stored names for a browser-facing credential name."""
        candidates = [name]
        if not is_password_only_label(name):
            candidates.append(encode_password_only_label(name))
        return candidates

    def _send_apdu(self, ins: int, p1: int = 0, p2: int = 0, data: bytes = b'') -> bytes:
        """Send a single APDU and return the payload (no more-data handling).

        SW is expected as the first 2 bytes of the device response.
        """
        raw = self._raw_send(self._build_apdu(ins, p1, p2, data))
        if len(raw) < 2:
            raise OATHError(f"Response too short: {raw.hex()}")
        sw = (raw[0] << 8) | raw[1]
        if sw == SW_SUCCESS:
            return raw[2:]
        if (sw & 0xFF00) == SW_MORE_DATA_MASK:
            return raw[2:]  # caller handles continuation
        self._interpret_sw(sw)

    def _send_apdu_all(self, ins: int, p1: int = 0, p2: int = 0, data: bytes = b'') -> bytes:
        """Send APDU and collect all data, issuing SEND_REMAINING on 0x61xx."""
        all_data = b''
        current_apdu = self._build_apdu(ins, p1, p2, data)

        while True:
            raw = self._raw_send(current_apdu)
            if len(raw) < 2:
                raise OATHError(f"Response too short: {raw.hex()}")
            sw = (raw[0] << 8) | raw[1]
            all_data += raw[2:]

            if sw == SW_SUCCESS:
                break
            if (sw & 0xFF00) == SW_MORE_DATA_MASK:
                current_apdu = self._build_apdu(INS_SEND_REMAINING)
                continue
            self._interpret_sw(sw)

        return all_data

    # ------------------------------------------------------------------
    # High-level operations

    def list_credentials(self) -> list:
        """Return list of credential dicts."""
        # P1=0, data=[1] requests version-1 format (with properties byte)
        payload = self._send_apdu_all(INS_LIST, 0x00, 0x00, bytes([1]))
        credentials = []
        for tag, value in self._parse_tlv(payload):
            if tag != TAG_NAME_LIST or len(value) < 2:
                continue
            kind_algo = value[0]
            props = value[-1]
            raw_name = value[1:-1].decode('utf-8', errors='replace')
            name = strip_password_only_label(raw_name)

            cred_type = 'TOTP' if (kind_algo & 0xF0) == KIND_TOTP else 'HOTP'
            if (kind_algo & 0xF0) == KIND_REVERSE_HOTP:
                cred_type = 'REVERSE_HOTP'
            elif (kind_algo & 0xF0) == KIND_HMAC:
                cred_type = 'HMAC'
            algorithm = {
                ALGO_SHA1: 'SHA1',
                ALGO_SHA256: 'SHA256',
                ALGO_SHA512: 'SHA512',
            }.get(kind_algo & 0x0F, 'SHA1')

            # Note: LIST response uses different bit positions than PUT command:
            # LIST response: bit 0 (0x01) = touch_required, bit 1 (0x02) = encrypted
            # PUT command: bit 1 (0x02) = touch_required, bit 2 (0x04) = encrypted
            effective_type = 'PASSWORD' if is_password_only_label(raw_name) else cred_type
            credentials.append({
                'name': name,
                'rawName': raw_name,
                'type': effective_type,
                'kind': effective_type,
                'algorithm': algorithm,
                'digits': 6,
                'touchRequired': bool(props & 0x01),  # bit 0 in LIST response
                'pinEncrypted': bool(props & 0x02),   # bit 1 in LIST response
                'hasPasswordSafe': bool(props & 0x04),
                'passwordOnly': is_password_only_label(raw_name),
            })
        return credentials

    def list_secrets(self) -> list:
        return self.list_credentials()

    def calculate_otp(self, name: str, period: int = 30) -> str:
        """Return OTP string.  Raises OATHTouchRequired or OATHPINRequired."""
        name_bytes = name.encode('utf-8')
        challenge = struct.pack('>Q', int(time.time()) // period)
        data = (self._build_tlv(TAG_NAME, name_bytes) +
                self._build_tlv(TAG_CHALLENGE, challenge))
        # P2=0x01 requests full-digit response
        payload = self._send_apdu(INS_CALCULATE, 0x00, 0x01, data)

        for tag, value in self._parse_tlv(payload):
            if tag == TAG_RESPONSE and len(value) >= 5:
                digits = value[0]
                code_int = int.from_bytes(value[1:5], 'big') & 0x7FFFFFFF
                return str(code_int % (10 ** digits)).zfill(digits)

        raise OATHError("Invalid calculate response")

    def verify_pin(self, pin: str) -> dict:
        data = self._build_tlv(TAG_PASSWORD, pin.encode('utf-8'))
        try:
            self._send_apdu(INS_VERIFY_PIN, 0x00, 0x00, data)
            return {'success': True}
        except OATHError as e:
            msg = str(e)
            m = re.search(r'(\d+) retries', msg)
            if m:
                return {'success': False, 'error': msg, 'attempts': int(m.group(1))}
            return {'success': False, 'error': msg}

    def set_pin(self, pin: str) -> dict:
        data = self._build_tlv(TAG_PASSWORD, pin.encode('utf-8'))
        try:
            self._send_apdu(INS_SET_PIN, 0x00, 0x00, data)
            return {'success': True}
        except OATHError as e:
            return {'success': False, 'error': str(e)}

    def change_pin(self, old_pin: str, new_pin: str) -> dict:
        data = (self._build_tlv(TAG_PASSWORD, old_pin.encode('utf-8')) +
                self._build_tlv(TAG_NEW_PASSWORD, new_pin.encode('utf-8')))
        try:
            self._send_apdu(INS_CHANGE_PIN, 0x00, 0x00, data)
            return {'success': True}
        except OATHError as e:
            msg = str(e)
            m = re.search(r'(\d+) retries', msg)
            if m:
                return {'success': False, 'error': msg, 'attempts': int(m.group(1))}
            return {'success': False, 'error': msg}

    def add_credential(self, name: str, secret_b32: str, type_: str,
                       algorithm: str, digits: int,
                       touch_required: bool, pin_protected: bool,
                       login: str | None = None,
                       password: str | None = None,
                       metadata: str | None = None,
                       password_only: bool = False) -> dict:
        # Normalise and decode the base32 secret
        secret_b32 = secret_b32.upper().replace(' ', '').replace('-', '')
        padding = (8 - len(secret_b32) % 8) % 8
        secret_b32 += '=' * padding
        try:
            secret_bytes = base64.b32decode(secret_b32)
        except Exception as e:
            return {'success': False, 'error': f'Invalid base32 secret: {e}'}

        kind = KIND_TOTP if type_.upper() == 'TOTP' else KIND_HOTP
        algo = {
            'SHA1': ALGO_SHA1, 'SHA256': ALGO_SHA256, 'SHA512': ALGO_SHA512
        }.get(algorithm.upper(), ALGO_SHA1)
        kind_algo = kind | algo

        # KEY TLV: kind_algo + digits + secret
        key_data = bytes([kind_algo, digits]) + secret_bytes
        stored_name = encode_password_only_label(name) if password_only else name
        data = (self._build_tlv(TAG_NAME, stored_name.encode('utf-8')) +
                self._build_tlv(TAG_KEY, key_data))

        if touch_required or pin_protected:
            props = 0
            if touch_required:
                props |= PROP_TOUCH_REQUIRED
            if pin_protected:
                props |= PROP_PIN_ENCRYPTED
            # Note: Property tag uses compact 2-byte format [tag, value], NOT TLV [tag, length, value]
            data += bytes([TAG_PROPERTY, props])

        if login is not None:
            data += self._build_tlv(TAG_PWS_LOGIN, login.encode('utf-8'))
        if password is not None:
            data += self._build_tlv(TAG_PWS_PASSWORD, password.encode('utf-8'))
        if metadata is not None:
            data += self._build_tlv(TAG_PWS_METADATA, metadata.encode('utf-8'))

        try:
            # Note: fido2 library handles touch waiting internally -
            # it blocks until device responds (including waiting for user touch)
            self._send_apdu(INS_PUT, 0x00, 0x00, data)
            return {'success': True}
        except OATHTouchRequired:
            return {'success': False, 'error': 'TOUCH_REQUIRED'}
        except OATHPINRequired:
            return {'success': False, 'error': 'PIN_REQUIRED'}
        except OATHError as e:
            return {'success': False, 'error': str(e)}

    def delete_credential(self, name: str) -> dict:
        last_error: OATHError | None = None
        for candidate in self._name_candidates(name):
            data = self._build_tlv(TAG_NAME, candidate.encode('utf-8'))
            try:
                self._send_apdu(INS_DELETE, 0x00, 0x00, data)
                return {'success': True}
            except OATHError as e:
                last_error = e
                if "0x6A82" not in str(e) or candidate == self._name_candidates(name)[-1]:
                    break
        return {'success': False, 'error': str(last_error) if last_error else 'Delete failed'}

    def get_password_entry(self, name: str) -> dict:
        payload = None
        last_error: Exception | None = None
        selected_name = name
        for candidate in self._name_candidates(name):
            data = self._build_tlv(TAG_NAME, candidate.encode('utf-8'))
            try:
                payload = self._send_apdu_all(INS_GET_CREDENTIAL, 0x00, 0x00, data)
                selected_name = candidate
                break
            except OATHTouchRequired:
                return {'success': False, 'error': 'TOUCH_REQUIRED'}
            except OATHPINRequired:
                return {'success': False, 'error': 'PIN_REQUIRED'}
            except OATHError as e:
                last_error = e
                if "0x6A82" not in str(e) or candidate == self._name_candidates(name)[-1]:
                    return {'success': False, 'error': str(e)}

        entry = {
            'name': strip_password_only_label(selected_name),
            'login': '',
            'password': '',
            'metadata': '',
        }
        for tag, value in self._parse_tlv(payload):
            if tag == TAG_NAME:
                entry['name'] = strip_password_only_label(
                    value.decode('utf-8', errors='replace')
                )
            elif tag == TAG_PWS_LOGIN:
                entry['login'] = value.decode('utf-8', errors='replace')
            elif tag == TAG_PWS_PASSWORD:
                entry['password'] = value.decode('utf-8', errors='replace')
            elif tag == TAG_PWS_METADATA:
                entry['metadata'] = value.decode('utf-8', errors='replace')
        return {'success': True, 'credential': entry}

    def update_password_entry(
        self,
        name: str,
        *,
        new_name: str | None = None,
        login: str | None = None,
        password: str | None = None,
        metadata: str | None = None,
    ) -> dict:
        last_error: Exception | None = None
        for candidate in self._name_candidates(name):
            data = self._build_tlv(TAG_NAME, candidate.encode('utf-8'))
            if new_name:
                target_name = (
                    encode_password_only_label(new_name)
                    if is_password_only_label(candidate)
                    else new_name
                )
                data += self._build_tlv(TAG_NAME, target_name.encode('utf-8'))
            if login is not None:
                data += self._build_tlv(TAG_PWS_LOGIN, login.encode('utf-8'))
            if password is not None:
                data += self._build_tlv(TAG_PWS_PASSWORD, password.encode('utf-8'))
            if metadata is not None:
                data += self._build_tlv(TAG_PWS_METADATA, metadata.encode('utf-8'))
            try:
                self._send_apdu(INS_UPDATE_CREDENTIAL, 0x00, 0x00, data)
                return {'success': True}
            except OATHTouchRequired:
                return {'success': False, 'error': 'TOUCH_REQUIRED'}
            except OATHPINRequired:
                return {'success': False, 'error': 'PIN_REQUIRED'}
            except OATHError as e:
                last_error = e
                if "0x6A82" not in str(e) or candidate == self._name_candidates(name)[-1]:
                    return {'success': False, 'error': str(e)}
        return {'success': False, 'error': str(last_error) if last_error else 'Update failed'}
