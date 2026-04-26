"""Singleton device manager for centralized, thread-safe device access."""

import logging
import os
import struct
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal
from fido2.ctap2 import Ctap2
from fido2.ctap2.credman import CredentialManagement
from fido2.ctap2.pin import ClientPin
from fido2.hid import CTAPHID
from solo2.secrets import SecretsAppProtocol, SecretsSession

from .models.device import SoloDevice

_log = logging.getLogger("solo2device")


class RequestType(Enum):
    """Types of device requests."""
    GET_INFO = auto()
    GET_PIN_RETRIES = auto()
    WINK = auto()
    VENDOR_COMMAND = auto()
    RESET = auto()
    GET_CREDENTIALS = auto()
    DELETE_CREDENTIAL = auto()
    RENAME_CREDENTIAL = auto()
    SET_PIN = auto()
    CHANGE_PIN = auto()
    BROWSER_APDU = auto()


@dataclass
class DeviceRequest:
    """A request to execute on the device."""
    request_type: RequestType
    callback: Optional[Callable[[Any, Optional[str]], None]]
    args: Dict[str, Any]
    operation_id: str = ""


class DeviceManager(QObject):
    """Singleton device manager for thread-safe device access."""
    
    _instance: Optional['DeviceManager'] = None
    _lock = threading.Lock()
    
    # Public signals for UI updates
    device_connected = Signal(str)
    device_disconnected = Signal()
    operation_started = Signal(str, str)
    operation_progress = Signal(str, int, str)
    operation_completed = Signal(str, bool, str)
    error_occurred = Signal(str, str)
    credentials_loaded = Signal(str, list)
    pin_status_updated = Signal(str, dict)
    pin_required = Signal(str)
    
    @classmethod
    def get_instance(cls) -> 'DeviceManager':
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = DeviceManager()
        return cls._instance
    
    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._ctap2: Optional[Ctap2] = None
        self._last_auth_error: Optional[str] = None
        self._request_queue: Queue = Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._mutex = threading.Lock()
    
    def start(self, device: SoloDevice) -> bool:
        """Start the device manager with the given device."""
        with self._lock:
            if self._running:
                current_path = getattr(self._device, "path", None)
                new_path = getattr(device, "path", None)
                if self._device is device:
                    return True

                _log.debug(
                    "start(): switching DeviceManager device old=%r new=%r same_path=%s",
                    current_path,
                    new_path,
                    current_path == new_path,
                )
                self._running = False
                self._request_queue.put(None)

                if self._worker_thread:
                    self._worker_thread.join(timeout=2.0)
                    self._worker_thread = None

                self._close_device()
                self._request_queue = Queue()

            self._device = device

            self._running = True
            self._request_queue = Queue()
            self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
            self._worker_thread.start()

            self.device_connected.emit(device.path)
            return True
    
    def stop(self):
        """Stop the device manager."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            self._request_queue.put(None)
            
            if self._worker_thread:
                self._worker_thread.join(timeout=2.0)
                self._worker_thread = None
            
            self._close_device()
            self._request_queue = Queue()
            self.device_disconnected.emit()
    
    def _open_ctap2(self) -> Optional[Ctap2]:
        """Open a fresh CTAP2 connection for one high-level operation."""
        try:
            if self._device is None:
                return None
            if hasattr(self._device, "prefers_ccid") and self._device.prefers_ccid():
                _log.debug("_open_device: CCID-backed device=%r", getattr(self._device, "path", None))
                return None
            _log.debug("_open_device: looking for device=%r", getattr(self._device, "path", None))
            ctap2 = self._device.open_ctap2()
            if ctap2 is not None:
                self._ctap2 = ctap2
                self._reset_hid_channel(ctap2)
                _log.debug("_open_device: SUCCESS")
                return ctap2
            _log.debug("_open_device: FAILED — device.open_ctap2() returned None")
            return None
        except Exception as e:
            _log.debug("_open_device: exception: %s", e)
            raise
    
    def _close_device(self):
        """Close any currently active CTAP2 connection and clear operation state."""
        try:
            if self._ctap2 is not None and getattr(self._ctap2, "device", None) is not None:
                close = getattr(self._ctap2.device, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            _log.debug("_close_device: close failed: %s", exc)
        self._ctap2 = None

    def _reset_hid_channel(self, ctap2: Optional[Ctap2] = None) -> None:
        """Re-initialize the CTAP HID channel for the current device, if any."""
        try:
            ctap2 = ctap2 or self._ctap2
            if ctap2:
                hid_dev = ctap2.device
                hid_dev._channel_id = 0xFFFFFFFF  # Broadcast channel
                nonce = os.urandom(8)
                response = hid_dev.call(CTAPHID.INIT, nonce)
                if response[:8] == nonce:
                    (hid_dev._channel_id,) = struct.unpack_from(">I", response, 8)
        except Exception as exc:
            _log.debug("_reset_hid_channel failed: %s", exc)

    def _run_with_fresh_ctap2(self, operation: Callable[[Ctap2], Any], *, retry: bool = True) -> Any:
        """Run one high-level CTAP2 operation with a short-lived HID handle."""
        if self._device is None:
            raise RuntimeError("Device not connected")

        attempts = 2 if retry else 1
        last_error: Optional[BaseException] = None

        for attempt in range(attempts):
            self._close_device()
            try:
                ctap2 = self._open_ctap2()
                if ctap2 is None:
                    raise RuntimeError("CTAP HID connection is not available")
                return operation(ctap2)
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= attempts or not self._is_retryable_channel_error(exc):
                    raise
                _log.debug("_run_with_fresh_ctap2: retrying after stale channel: %s", exc)
                time.sleep(0.1)
            finally:
                self._close_device()

        if last_error is not None:
            raise last_error
        raise RuntimeError("CTAP HID connection is not available")

    @staticmethod
    def _is_retryable_channel_error(error: BaseException | str) -> bool:
        """Return True for stale CTAP HID channel/handle errors."""
        error_msg = str(error).lower()
        return (
            "wrong channel" in error_msg
            or "wrong_channel" in error_msg
            or "invalid channel" in error_msg
            or "invalid_channel" in error_msg
            or "invalid command" in error_msg
            or "invalid_command" in error_msg
            or "invalid_seq" in error_msg
            or "key_agreement" in error_msg
            or "result.key_agreement" in error_msg
            or "ctap error: 0x01" in error_msg
            or "ctap error: 0x04" in error_msg
            or "6f00" in error_msg
            or "0x6f00" in error_msg
            or "device not available" in error_msg
            or "not connected" in error_msg
        )

    @staticmethod
    def _normalize_pin_retries(raw_retries: Any) -> Optional[int]:
        """Extract a valid integer retry count from fido2 version-specific results."""
        if isinstance(raw_retries, tuple):
            raw_retries = raw_retries[0] if raw_retries else None
        if isinstance(raw_retries, dict):
            raw_retries = raw_retries.get(ClientPin.RESULT.PIN_RETRIES)
        if isinstance(raw_retries, bool):
            return int(raw_retries)
        if isinstance(raw_retries, int):
            return raw_retries
        return None

    @staticmethod
    def _is_key_agreement_result_error(error: BaseException | str) -> bool:
        error_msg = str(error).lower()
        return "key_agreement" in error_msg or "result.key_agreement" in error_msg
    
    def _process_loop(self):
        """Main processing loop - runs in worker thread."""
        while self._running:
            try:
                request = self._request_queue.get(timeout=0.1)
                if request is None:
                    break
                self._handle_request(request)
            except Empty:
                continue
            except Exception as e:
                print(f"[DeviceManager] Error in process loop: {e}")
    
    def _handle_request(self, request: DeviceRequest):
        """Handle a single request."""
        try:
            self.operation_started.emit(request.operation_id, request.request_type.name)
            
            if request.request_type == RequestType.GET_INFO:
                self._do_get_info(request)
            elif request.request_type == RequestType.GET_PIN_RETRIES:
                self._do_get_pin_retries(request)
            elif request.request_type == RequestType.WINK:
                self._do_wink(request)
            elif request.request_type == RequestType.VENDOR_COMMAND:
                self._do_vendor_command(request)
            elif request.request_type == RequestType.RESET:
                self._do_reset(request)
            elif request.request_type == RequestType.GET_CREDENTIALS:
                self._do_get_credentials(request)
            elif request.request_type == RequestType.DELETE_CREDENTIAL:
                self._do_delete_credential(request)
            elif request.request_type == RequestType.RENAME_CREDENTIAL:
                self._do_rename_credential(request)
            elif request.request_type == RequestType.SET_PIN:
                self._do_set_pin(request)
            elif request.request_type == RequestType.CHANGE_PIN:
                self._do_change_pin(request)
            elif request.request_type == RequestType.BROWSER_APDU:
                self._do_browser_apdu(request)
        except Exception as e:
            if request.callback:
                request.callback(None, str(e))
            self.error_occurred.emit(request.operation_id, str(e))
    
    def _prefers_ccid(self) -> bool:
        return bool(
            self._device is not None
            and hasattr(self._device, "prefers_ccid")
            and self._device.prefers_ccid()
        )

    def _credential_management_for_pin(self, ctap2: Ctap2, pin: str) -> CredentialManagement:
        """Create an authenticated CredentialManagement object for this operation."""
        self._last_auth_error = None

        permissions = [ClientPin.PERMISSION.CREDENTIAL_MGMT]
        persistent_cred_mgmt = getattr(
            ClientPin.PERMISSION, "PERSISTENT_CREDENTIAL_MGMT", None
        )
        if persistent_cred_mgmt is not None:
            permissions.append(persistent_cred_mgmt)
        # Some authenticators advertise pinUvAuthToken but fail the
        # permission-scoped token path. Fall back to the legacy token flow.
        permissions.append(None)

        last_error: Exception | None = None
        for permission in permissions:
            try:
                client_pin = ClientPin(ctap2)
                pin_protocol = client_pin.protocol
                pin_token = client_pin.get_pin_token(pin, permission)
                self._last_auth_error = None
                return CredentialManagement(ctap2, pin_protocol, pin_token)
            except Exception as exc:
                last_error = exc
                _log.debug(
                    "_credential_management_for_pin failed permission=%s err=%s",
                    permission,
                    exc,
                )
                if self._is_key_agreement_result_error(exc):
                    break

        if last_error is None:
            self._last_auth_error = "Authentication failed"
        else:
            message = str(last_error).strip() or last_error.__class__.__name__
            self._last_auth_error = message
        raise RuntimeError(self._last_auth_error)
    
    def _do_get_info(self, request: DeviceRequest):
        """Execute GET_INFO request."""
        try:
            if self._prefers_ccid():
                info = self._device.get_info()
                result = {
                    'aaguid': None,
                    'versions': [],
                    'options': {},
                    'firmware_version': info.firmware_version,
                    'ctap2_available': False,
                }
                request.callback(result, None)
                return

            _log.debug("_do_get_info: calling ctap2.get_info()")
            info = self._run_with_fresh_ctap2(lambda ctap2: ctap2.get_info())
            opts = dict(info.options) if info.options else {}
            _log.debug("_do_get_info: OK versions=%s options=%s", info.versions, opts)
            result = {
                'aaguid': info.aaguid,
                'versions': info.versions,
                'options': opts,
                'ctap2_available': True,
            }
            request.callback(result, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_get_pin_retries(self, request: DeviceRequest):
        """Execute GET_PIN_RETRIES request."""
        try:
            def operation(ctap2: Ctap2) -> Optional[int]:
                client_pin = ClientPin(ctap2)
                return self._normalize_pin_retries(client_pin.get_pin_retries())

            retries = self._run_with_fresh_ctap2(operation)
            request.callback(retries, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_wink(self, request: DeviceRequest):
        """Execute WINK request."""
        try:
            if self._prefers_ccid():
                self._device.admin().wink()
                request.callback(True, None)
                return

            self._run_with_fresh_ctap2(lambda ctap2: ctap2.device.wink())
            request.callback(True, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_vendor_command(self, request: DeviceRequest):
        """Execute vendor command."""
        try:
            command = request.args['command']
            data = request.args['data']
            if self._prefers_ccid():
                if command == 0x70:
                    response = self._device.secrets().send_apdu(data)
                else:
                    response = self._device.admin().call(command, data)
                request.callback(response, None)
                return

            response = self._run_with_fresh_ctap2(
                lambda ctap2: ctap2.device.call(command, data)
            )
            request.callback(response, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_reset(self, request: DeviceRequest):
        """Execute RESET request."""
        if self._device is None:
            request.callback(None, "Device not connected")
            return

        try:
            _log.debug("_do_reset: issuing CTAP authenticatorReset")
            self._run_with_fresh_ctap2(lambda ctap2: ctap2.reset(), retry=False)
            verification_error = self._verify_factory_reset_effect()
            if verification_error:
                request.callback(None, verification_error)
                return
            request.callback(True, None)
        except Exception as e:
            request.callback(None, str(e))

    def _open_ctap2_for_reset_verification(self, timeout: float = 3.0) -> Optional[Ctap2]:
        """Open a fresh CTAP2 handle with a short retry window after reset."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._close_device()
            try:
                ctap2 = self._device.open_ctap2() if self._device is not None else None
                if ctap2 is not None:
                    self._ctap2 = ctap2
                    self._reset_hid_channel(ctap2)
                    return ctap2
            except Exception as exc:
                _log.debug("_open_ctap2_for_reset_verification failed: %s", exc)
            time.sleep(0.2)
        return None

    def _verify_factory_reset_effect(self) -> Optional[str]:
        """Verify that reset cleared FIDO2 state and reset the Secrets applet if present."""
        if self._device is None:
            return "Device disconnected before reset verification"

        ctap2 = self._open_ctap2_for_reset_verification()
        if ctap2 is None:
            return "Factory reset completed, but the device could not be reopened for verification"

        try:
            info = ctap2.get_info()
            options = dict(info.options) if info.options else {}
            if bool(options.get("clientPin")):
                _log.debug("_verify_factory_reset_effect: clientPin still enabled after reset")
                return "Factory reset did not clear the FIDO2 PIN state"
            _log.debug("_verify_factory_reset_effect: FIDO2 reset verified options=%s", options)
        except Exception as exc:
            _log.debug("_verify_factory_reset_effect: FIDO2 verification failed: %s", exc)
            return f"Factory reset completed, but FIDO2 verification failed: {exc}"
        finally:
            self._close_device()

        try:
            session = SecretsSession(device=self._device)
            status_before = session.get_status()
            _log.debug(
                "_verify_factory_reset_effect: secrets status before reset-supported cleanup supported=%s pin_set=%s count=%s",
                status_before.supported,
                status_before.pin_set,
                status_before.credentials_count,
            )
            if not status_before.supported:
                return None

            session._send_apdu(SecretsAppProtocol.INS_RESET, p1=0xDE, p2=0xAD)
            time.sleep(0.2)
            status_after = session.get_status()
            _log.debug(
                "_verify_factory_reset_effect: secrets status after reset supported=%s pin_set=%s count=%s",
                status_after.supported,
                status_after.pin_set,
                status_after.credentials_count,
            )
            if status_after.pin_set:
                return "Secrets reset did not clear the Secrets PIN"
            if status_after.credentials_count:
                return (
                    f"Secrets reset did not clear stored credentials "
                    f"({status_after.credentials_count} still present)"
                )
        except Exception as exc:
            _log.debug("_verify_factory_reset_effect: secrets reset/verification failed: %s", exc)
            return f"FIDO2 reset completed, but Secrets reset failed: {exc}"

        return None

    def _collect_resident_credentials(self, credman: CredentialManagement, operation_id: str) -> list[dict]:
        """Read resident credentials through an authenticated CredentialManagement session."""
        credentials = []
        metadata = credman.get_metadata()
        existing_count = metadata.get(CredentialManagement.RESULT.EXISTING_CRED_COUNT, 0)

        if existing_count > 0:
            self.operation_progress.emit(operation_id, 10, f"Found {existing_count} credentials")
            rps = credman.enumerate_rps()

            for idx, rp_data in enumerate(rps):
                rp = rp_data.get(CredentialManagement.RESULT.RP)
                rp_id_hash = rp_data.get(CredentialManagement.RESULT.RP_ID_HASH)

                if not rp:
                    continue

                creds = credman.enumerate_creds(rp_id_hash)
                for cred_data in creds:
                    cred_id = cred_data.get(CredentialManagement.RESULT.CREDENTIAL_ID)
                    user = cred_data.get(CredentialManagement.RESULT.USER)

                    if cred_id and user:
                        credentials.append({
                            'cred_id': cred_id,
                            'rp_id': rp.get('id', ''),
                            'rp_name': rp.get('name', ''),
                            'user_id': user.get('id', b'').hex() if isinstance(user.get('id'), bytes) else str(user.get('id', '')),
                            'user_name': user.get('name', ''),
                            'user_display_name': user.get('displayName', ''),
                        })

                progress = int(10 + (idx + 1) / len(rps) * 80)
                self.operation_progress.emit(operation_id, progress, f"Loading credentials from {rp.get('name', 'Unknown')}...")

        return credentials
    
    def _do_get_credentials(self, request: DeviceRequest):
        """Execute GET_CREDENTIALS request."""
        pin = request.args.get('pin')
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return

        try:
            def operation(ctap2: Ctap2) -> list[dict]:
                credman = self._credential_management_for_pin(ctap2, pin)
                return self._collect_resident_credentials(credman, request.operation_id)

            credentials = self._run_with_fresh_ctap2(operation)
            self.credentials_loaded.emit(request.operation_id, credentials)
            request.callback(credentials, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_delete_credential(self, request: DeviceRequest):
        """Execute DELETE_CREDENTIAL request."""
        pin = request.args.get('pin')
        cred_id = request.args.get('cred_id')
        
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return

        try:
            def operation(ctap2: Ctap2) -> bool:
                credman = self._credential_management_for_pin(ctap2, pin)
                credman.delete_cred(cred_id)
                return True

            self._run_with_fresh_ctap2(operation, retry=False)
            request.callback(True, None)
        except Exception as e:
            request.callback(False, str(e))
    
    def _do_rename_credential(self, request: DeviceRequest):
        """Execute RENAME_CREDENTIAL request."""
        pin = request.args.get('pin')
        cred_id = request.args.get('cred_id')
        new_name = request.args.get('new_name')
        
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return

        try:
            def operation(ctap2: Ctap2) -> bool:
                credman = self._credential_management_for_pin(ctap2, pin)
                cred_id_descriptor = {"id": cred_id, "type": "public-key"}
                user_info = {
                    "id": request.args.get('user_id', b''),
                    "name": new_name,
                    "displayName": new_name,
                }
                credman.update_user_info(cred_id_descriptor, user_info)
                return True

            self._run_with_fresh_ctap2(operation, retry=False)
            request.callback(True, None)
        except Exception as e:
            request.callback(False, str(e))
    
    def _do_set_pin(self, request: DeviceRequest):
        """Execute SET_PIN request."""
        new_pin = request.args.get('new_pin')

        try:
            _log.debug("_do_set_pin: calling ClientPin.set_pin()")
            def operation(ctap2: Ctap2) -> bool:
                client_pin = ClientPin(ctap2)
                client_pin.set_pin(new_pin)
                return True

            self._run_with_fresh_ctap2(operation, retry=False)
            _log.debug("_do_set_pin: OK")
            request.callback(True, None)
        except Exception as e:
            _log.debug("_do_set_pin: exception: %s", e)
            request.callback(False, str(e))
    
    def _do_browser_apdu(self, request: DeviceRequest):
        """Execute a browser APDU request (CTAPHID vendor command 0x70)."""
        try:
            apdu_bytes = request.args['apdu_bytes']
            if self._prefers_ccid():
                response = self._device.secrets().send_apdu(bytes(apdu_bytes))
                request.callback(response, None)
                return

            response = self._run_with_fresh_ctap2(
                lambda ctap2: ctap2.device.call(0x70, bytes(apdu_bytes))
            )
            request.callback(response, None)
        except Exception as e:
            request.callback(None, str(e))

    def _do_change_pin(self, request: DeviceRequest):
        """Execute CHANGE_PIN request."""
        current_pin = request.args.get('current_pin')
        new_pin = request.args.get('new_pin')

        try:
            def operation(ctap2: Ctap2) -> bool:
                client_pin = ClientPin(ctap2)
                client_pin.change_pin(current_pin, new_pin)
                return True

            self._run_with_fresh_ctap2(operation, retry=False)
            request.callback(True, None)
        except Exception as e:
            request.callback(False, str(e))
    
    # Public API Methods
    
    def submit_request(self, request: DeviceRequest):
        """Submit a request to the queue."""
        self._request_queue.put(request)
    
    def get_info(self, callback: Callable[[Dict, Optional[str]], None], operation_id: str = ""):
        """Queue a get_info request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.GET_INFO,
            callback=callback,
            args={},
            operation_id=operation_id
        ))
    
    def get_pin_retries(self, callback: Callable[[int, Optional[str]], None], operation_id: str = ""):
        """Queue a get_pin_retries request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.GET_PIN_RETRIES,
            callback=callback,
            args={},
            operation_id=operation_id
        ))
    
    def wink(self, callback: Callable[[bool, Optional[str]], None], operation_id: str = ""):
        """Queue a wink request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.WINK,
            callback=callback,
            args={},
            operation_id=operation_id
        ))
    
    def vendor_command(self, command: int, data: bytes,
                      callback: Callable[[bytes, Optional[str]], None], operation_id: str = ""):
        """Queue a vendor command request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.VENDOR_COMMAND,
            callback=callback,
            args={'command': command, 'data': data},
            operation_id=operation_id
        ))
    
    def reset(self, callback: Callable[[bool, Optional[str]], None], operation_id: str = ""):
        """Queue a reset request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.RESET,
            callback=callback,
            args={},
            operation_id=operation_id
        ))
    
    def get_credentials(self, pin: Optional[str] = None,
                       callback: Callable[[List, Optional[str]], None] = None,
                       operation_id: str = ""):
        """Queue a get_credentials request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.GET_CREDENTIALS,
            callback=callback,
            args={'pin': pin},
            operation_id=operation_id
        ))
    
    def delete_credential(self, cred_id: bytes, pin: Optional[str] = None,
                         callback: Callable[[bool, Optional[str]], None] = None,
                         operation_id: str = ""):
        """Queue a delete_credential request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.DELETE_CREDENTIAL,
            callback=callback,
            args={'cred_id': cred_id, 'pin': pin},
            operation_id=operation_id
        ))
    
    def rename_credential(self, cred_id: bytes, new_name: str, user_id: bytes,
                         pin: Optional[str] = None,
                         callback: Callable[[bool, Optional[str]], None] = None,
                         operation_id: str = ""):
        """Queue a rename_credential request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.RENAME_CREDENTIAL,
            callback=callback,
            args={'cred_id': cred_id, 'new_name': new_name, 'user_id': user_id, 'pin': pin},
            operation_id=operation_id
        ))
    
    def set_pin(self, new_pin: str,
               callback: Callable[[bool, Optional[str]], None] = None,
               operation_id: str = ""):
        """Queue a set_pin request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.SET_PIN,
            callback=callback,
            args={'new_pin': new_pin},
            operation_id=operation_id
        ))
    
    def change_pin(self, current_pin: str, new_pin: str,
                  callback: Callable[[bool, Optional[str]], None] = None,
                  operation_id: str = ""):
        """Queue a change_pin request."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.CHANGE_PIN,
            callback=callback,
            args={'current_pin': current_pin, 'new_pin': new_pin},
            operation_id=operation_id
        ))
    
    def send_browser_apdu(self, apdu_bytes: bytes,
                          callback: Callable[[bytes, Optional[str]], None],
                          operation_id: str = ""):
        """Queue a browser APDU request (CTAPHID vendor command 0x70)."""
        self.submit_request(DeviceRequest(
            request_type=RequestType.BROWSER_APDU,
            callback=callback,
            args={'apdu_bytes': apdu_bytes},
            operation_id=operation_id
        ))

    def set_cached_pin(self, pin: str):
        """Deprecated compatibility hook; FIDO2 PINs are no longer cached."""
        del pin
    
    def clear_cached_pin(self):
        """Deprecated compatibility hook; FIDO2 PINs are no longer cached."""
        pass
