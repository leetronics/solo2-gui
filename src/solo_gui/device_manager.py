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
        self._client_pin: Optional[ClientPin] = None
        self._pin_token: Optional[bytes] = None
        self._pin_protocol = None
        self._credman: Optional[CredentialManagement] = None
        self._cached_pin: Optional[str] = None
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
                if self._device is device or current_path == new_path:
                    return True

                _log.debug(
                    "start(): switching DeviceManager device old=%r new=%r",
                    current_path,
                    new_path,
                )
                self._running = False
                self._cached_pin = None
                self._request_queue.put(None)

                if self._worker_thread:
                    self._worker_thread.join(timeout=2.0)
                    self._worker_thread = None

                self._close_device()
                self._request_queue = Queue()

            self._device = device
            if not self._open_device():
                return False

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
            self._cached_pin = None
            self._request_queue.put(None)
            
            if self._worker_thread:
                self._worker_thread.join(timeout=2.0)
                self._worker_thread = None
            
            self._close_device()
            self._request_queue = Queue()
            self.device_disconnected.emit()
    
    def _open_device(self) -> bool:
        """Open the device connection."""
        try:
            if self._device is None:
                return False
            if hasattr(self._device, "prefers_ccid") and self._device.prefers_ccid():
                _log.debug("_open_device: CCID-backed device=%r", getattr(self._device, "path", None))
                self._ctap2 = None
                return True
            _log.debug("_open_device: looking for device=%r", getattr(self._device, "path", None))
            self._ctap2 = self._device.open_ctap2()
            if self._ctap2 is not None:
                self._reset_hid_channel()
                _log.debug("_open_device: SUCCESS")
                return True
            _log.debug("_open_device: FAILED — device.open_ctap2() returned None")
            return False
        except Exception as e:
            # CTAP error 0x00 is actually success, device is already open
            if "0x00" in str(e) or "SUCCESS" in str(e):
                return True
            _log.debug("_open_device: exception: %s", e)
            return False
    
    def _close_device(self):
        """Close the device connection."""
        self._ctap2 = None
        self._client_pin = None
        self._pin_token = None
        self._credman = None

    def _reset_hid_channel(self) -> None:
        """Re-initialize the CTAP HID channel for the current device, if any."""
        try:
            if self._ctap2:
                hid_dev = self._ctap2.device
                hid_dev._channel_id = 0xFFFFFFFF  # Broadcast channel
                nonce = os.urandom(8)
                response = hid_dev.call(CTAPHID.INIT, nonce)
                if response[:8] == nonce:
                    (hid_dev._channel_id,) = struct.unpack_from(">I", response, 8)
        except Exception as exc:
            _log.debug("_reset_hid_channel failed: %s", exc)

    def _reopen_device(self) -> bool:
        """Reopen device connection (after error)."""
        self._close_device()
        time.sleep(0.1)  # Brief delay to let USB settle
        if not self._open_device():
            return False
        return True
    
    def _wait_for_device(self, timeout: float = 10.0) -> bool:
        """Wait for device to reappear after disconnect."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._device is not None:
                    self._ctap2 = self._device.open_ctap2()
                    if self._ctap2 is not None:
                        return True
            except Exception:
                pass
            time.sleep(0.25)
        return False
    
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
    
    def _ensure_device(self) -> bool:
        """Ensure device is connected, reopen if needed."""
        if self._device is not None and hasattr(self._device, "prefers_ccid") and self._device.prefers_ccid():
            return True
        if self._ctap2 is None:
            return self._reopen_device()
        return True
    
    def _ensure_authenticated(self, pin: str) -> bool:
        """Ensure we have valid PIN token for CredMan operations."""
        self._last_auth_error = None
        if self._ctap2 is None:
            if not self._reopen_device():
                self._last_auth_error = "Device not connected"
                return False

        permissions = [ClientPin.PERMISSION.CREDENTIAL_MGMT]
        persistent_cred_mgmt = getattr(
            ClientPin.PERMISSION, "PERSISTENT_CREDENTIAL_MGMT", None
        )
        if persistent_cred_mgmt is not None:
            permissions.append(persistent_cred_mgmt)

        last_error: Exception | None = None
        for attempt in range(2):
            self._client_pin = None
            self._pin_token = None
            self._credman = None

            for permission in permissions:
                try:
                    self._client_pin = ClientPin(self._ctap2)
                    self._pin_protocol = self._client_pin.protocol
                    self._pin_token = self._client_pin.get_pin_token(pin, permission)
                    self._cached_pin = pin
                    self._credman = CredentialManagement(
                        self._ctap2, self._pin_protocol, self._pin_token
                    )
                    self._last_auth_error = None
                    return True
                except Exception as exc:
                    last_error = exc
                    _log.debug(
                        "_ensure_authenticated failed permission=%s err=%s",
                        permission,
                        exc,
                    )

            error_text = str(last_error).lower() if last_error else ""
            if "wrong channel" not in error_text and "wrong_channel" not in error_text:
                break
            if not self._reopen_device():
                break

        if last_error is None:
            self._last_auth_error = "Authentication failed"
        else:
            message = str(last_error).strip() or last_error.__class__.__name__
            self._last_auth_error = message
        return False
    
    def _do_get_info(self, request: DeviceRequest):
        """Execute GET_INFO request."""
        if not self._ensure_device():
            _log.debug("_do_get_info: _ensure_device() failed")
            request.callback(None, "Device not connected")
            return

        try:
            if self._ctap2 is None and self._device is not None and hasattr(self._device, "prefers_ccid") and self._device.prefers_ccid():
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
            info = self._ctap2.get_info()
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
            error_msg = str(e)
            if "wrong channel" in error_msg.lower():
                if self._reopen_device():
                    try:
                        info = self._ctap2.get_info()
                        result = {
                            'aaguid': info.aaguid,
                            'versions': info.versions,
                            'options': dict(info.options) if info.options else {},
                            'ctap2_available': True,
                        }
                        request.callback(result, None)
                        return
                    except Exception as e2:
                        request.callback(None, str(e2))
                        return
            request.callback(None, error_msg)
    
    def _do_get_pin_retries(self, request: DeviceRequest):
        """Execute GET_PIN_RETRIES request."""
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return
        
        try:
            if self._client_pin is None:
                self._client_pin = ClientPin(self._ctap2)
            retries = self._client_pin.get_pin_retries()[0]
            request.callback(retries, None)
        except Exception as e:
            error_msg = str(e)
            if "wrong channel" in error_msg.lower():
                if self._reopen_device():
                    try:
                        if self._client_pin is None:
                            self._client_pin = ClientPin(self._ctap2)
                        retries = self._client_pin.get_pin_retries()[0]
                        request.callback(retries, None)
                        return
                    except Exception as e2:
                        request.callback(None, str(e2))
                        return
            request.callback(None, error_msg)
    
    def _do_wink(self, request: DeviceRequest):
        """Execute WINK request."""
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return
        
        try:
            if self._ctap2 is None and self._device is not None:
                self._device.admin().wink()
                request.callback(True, None)
                return
            self._ctap2.device.wink()
            request.callback(True, None)
        except Exception as e:
            error_msg = str(e)
            if "wrong_channel" in error_msg.lower():
                if self._reopen_device():
                    try:
                        self._ctap2.device.wink()
                        request.callback(True, None)
                        return
                    except Exception as e2:
                        request.callback(None, str(e2))
                        return
            request.callback(None, error_msg)
    
    def _do_vendor_command(self, request: DeviceRequest):
        """Execute vendor command."""
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return
        
        try:
            command = request.args['command']
            data = request.args['data']
            if self._ctap2 is None and self._device is not None:
                if command == 0x70:
                    response = self._device.secrets().send_apdu(data)
                else:
                    response = self._device.admin().call(command, data)
                request.callback(response, None)
                return
            response = self._ctap2.device.call(command, data)
            request.callback(response, None)
        except Exception as e:
            error_msg = str(e)
            retryable = (
                "wrong channel" in error_msg.lower()
                or "wrong_channel" in error_msg.lower()
                or "6f00" in error_msg.lower()
                or "0x6f00" in error_msg.lower()
                or "device not available" in error_msg.lower()
                or "not connected" in error_msg.lower()
            )
            if retryable and self._reopen_device():
                try:
                    command = request.args['command']
                    data = request.args['data']
                    if self._ctap2 is None and self._device is not None:
                        if command == 0x70:
                            response = self._device.secrets().send_apdu(data)
                        else:
                            response = self._device.admin().call(command, data)
                        request.callback(response, None)
                        return
                    response = self._ctap2.device.call(command, data)
                    request.callback(response, None)
                    return
                except Exception as e2:
                    request.callback(None, str(e2))
                    return
            request.callback(None, error_msg)
    
    def _do_reset(self, request: DeviceRequest):
        """Execute RESET request."""
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return
        
        try:
            self._ctap2.reset()
            self._cached_pin = None
            self._pin_token = None
            self._credman = None
            request.callback(True, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_get_credentials(self, request: DeviceRequest):
        """Execute GET_CREDENTIALS request."""
        pin = request.args.get('pin') or self._cached_pin
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return
        
        if not self._ensure_authenticated(pin):
            request.callback(None, self._last_auth_error or "Authentication failed")
            return
        
        try:
            credentials = []
            metadata = self._credman.get_metadata()
            existing_count = metadata.get(CredentialManagement.RESULT.EXISTING_CRED_COUNT, 0)
            
            if existing_count > 0:
                self.operation_progress.emit(request.operation_id, 10, f"Found {existing_count} credentials")
                rps = self._credman.enumerate_rps()
                
                for idx, rp_data in enumerate(rps):
                    rp = rp_data.get(CredentialManagement.RESULT.RP)
                    rp_id_hash = rp_data.get(CredentialManagement.RESULT.RP_ID_HASH)
                    
                    if not rp:
                        continue
                    
                    creds = self._credman.enumerate_creds(rp_id_hash)
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
                    self.operation_progress.emit(request.operation_id, progress, f"Loading credentials from {rp.get('name', 'Unknown')}...")
            
            self.credentials_loaded.emit(request.operation_id, credentials)
            request.callback(credentials, None)
        except Exception as e:
            request.callback(None, str(e))
    
    def _do_delete_credential(self, request: DeviceRequest):
        """Execute DELETE_CREDENTIAL request."""
        pin = request.args.get('pin') or self._cached_pin
        cred_id = request.args.get('cred_id')
        
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return
        
        if not self._ensure_authenticated(pin):
            request.callback(None, self._last_auth_error or "Authentication failed")
            return
        
        try:
            self._credman.delete_cred(cred_id)
            request.callback(True, None)
        except Exception as e:
            request.callback(False, str(e))
    
    def _do_rename_credential(self, request: DeviceRequest):
        """Execute RENAME_CREDENTIAL request."""
        pin = request.args.get('pin') or self._cached_pin
        cred_id = request.args.get('cred_id')
        new_name = request.args.get('new_name')
        
        if not pin:
            self.pin_required.emit(request.operation_id)
            request.callback(None, "PIN required")
            return
        
        if not self._ensure_authenticated(pin):
            request.callback(None, self._last_auth_error or "Authentication failed")
            return
        
        try:
            cred_id_descriptor = {"id": cred_id, "type": "public-key"}
            user_info = {
                "id": request.args.get('user_id', b''),
                "name": new_name,
                "displayName": new_name,
            }
            self._credman.update_user_info(cred_id_descriptor, user_info)
            request.callback(True, None)
        except Exception as e:
            request.callback(False, str(e))
    
    def _do_set_pin(self, request: DeviceRequest):
        """Execute SET_PIN request."""
        new_pin = request.args.get('new_pin')

        if not self._ensure_device():
            _log.debug("_do_set_pin: _ensure_device() failed")
            request.callback(None, "Device not connected")
            return

        try:
            _log.debug("_do_set_pin: calling ClientPin.set_pin()")
            if self._client_pin is None:
                self._client_pin = ClientPin(self._ctap2)
            self._client_pin.set_pin(new_pin)
            self._cached_pin = new_pin
            _log.debug("_do_set_pin: OK")
            request.callback(True, None)
        except Exception as e:
            _log.debug("_do_set_pin: exception: %s", e)
            request.callback(False, str(e))
    
    def _do_browser_apdu(self, request: DeviceRequest):
        """Execute a browser APDU request (CTAPHID vendor command 0x70)."""
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return

        try:
            apdu_bytes = request.args['apdu_bytes']
            if self._ctap2 is None and self._device is not None:
                response = self._device.secrets().send_apdu(bytes(apdu_bytes))
                request.callback(response, None)
                return
            response = self._ctap2.device.call(0x70, bytes(apdu_bytes))
            request.callback(response, None)
        except Exception as e:
            error_msg = str(e)
            retryable = (
                "wrong channel" in error_msg.lower()
                or "wrong_channel" in error_msg.lower()
                or "6f00" in error_msg.lower()
                or "0x6f00" in error_msg.lower()
                or "device not available" in error_msg.lower()
                or "not connected" in error_msg.lower()
            )
            if retryable and self._reopen_device():
                try:
                    apdu_bytes = request.args['apdu_bytes']
                    if self._ctap2 is None and self._device is not None:
                        response = self._device.secrets().send_apdu(bytes(apdu_bytes))
                        request.callback(response, None)
                        return
                    response = self._ctap2.device.call(0x70, bytes(apdu_bytes))
                    request.callback(response, None)
                    return
                except Exception as e2:
                    request.callback(None, str(e2))
                    return
            request.callback(None, error_msg)

    def _do_change_pin(self, request: DeviceRequest):
        """Execute CHANGE_PIN request."""
        current_pin = request.args.get('current_pin')
        new_pin = request.args.get('new_pin')
        
        if not self._ensure_device():
            request.callback(None, "Device not connected")
            return
        
        try:
            if self._client_pin is None:
                self._client_pin = ClientPin(self._ctap2)
            self._client_pin.change_pin(current_pin, new_pin)
            self._cached_pin = new_pin
            self._pin_token = None
            self._credman = None
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
        """Cache PIN for the session (memory only, cleared on disconnect)."""
        self._cached_pin = pin
    
    def clear_cached_pin(self):
        """Clear cached PIN."""
        self._cached_pin = None
