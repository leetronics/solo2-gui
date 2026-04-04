"""FIDO2 worker thread for SoloKeys GUI using DeviceManager."""

from typing import Optional

from PySide6.QtCore import QObject, Signal

from solo2.fido2 import Fido2Credential
from solo_gui.device_manager import DeviceManager


class Fido2Worker(QObject):
    """Worker thread for FIDO2 operations using DeviceManager."""

    credentials_loaded = Signal(list)
    credential_deleted = Signal(bool, str)
    credential_renamed = Signal(bool, str)
    pin_changed = Signal(bool, str)
    pin_status_updated = Signal(dict)
    pin_required = Signal()
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self._device_manager = DeviceManager.get_instance()
        self._pin: Optional[str] = None
        self._current_op_id: str = ""

    def set_pin(self, pin: str) -> None:
        """Set the PIN for authenticated operations."""
        self._pin = pin
        self._device_manager.set_cached_pin(pin)

    def get_pin_status(self) -> None:
        """Get current PIN status and retry counters."""
        self._current_op_id = "fido2_get_info"
        
        def on_info(result, error):
            if error:
                self.error_occurred.emit(f"Failed to get PIN status: {error}")
                return
            
            if result:
                ctap2_available = result.get('ctap2_available', True)
                status = {
                    'ctap2_available': ctap2_available,
                    'pin_set': result.get('options', {}).get('clientPin', False),
                    'pin_retries': None,
                    'uv_set': result.get('options', {}).get('uv', False),
                    'cred_mgmt_supported': bool(
                        result.get('options', {}).get('credMgmt', False)
                        or result.get('options', {}).get('credentialMgmtPreview', False)
                    ),
                }

                if not ctap2_available:
                    self.pin_status_updated.emit(status)
                    return
                
                # Get PIN retries separately if PIN is set
                if status['pin_set']:
                    def on_retries(retries, err):
                        if not err:
                            status['pin_retries'] = retries
                        self.pin_status_updated.emit(status)
                    
                    self._device_manager.get_pin_retries(on_retries, operation_id="fido2_pin_retries")
                else:
                    self.pin_status_updated.emit(status)
        
        self._device_manager.get_info(on_info, operation_id=self._current_op_id)

    def load_credentials(self, pin: Optional[str] = None) -> None:
        """Load all FIDO2 credentials from the device."""
        pin_to_use = pin or self._pin
        self._current_op_id = "fido2_load_creds"
        
        def on_loaded(credentials, error):
            if error:
                if "pin" in error.lower() or "PIN" in error:
                    self.pin_required.emit()
                else:
                    self.error_occurred.emit(f"Failed to load credentials: {error}")
                return
            
            # Convert raw credential dicts to Fido2Credential objects
            cred_objects = []
            for cred in credentials:
                cred_objects.append(Fido2Credential(
                    id=cred.get('cred_id', b'').hex() if isinstance(cred.get('cred_id'), bytes) else str(cred.get('cred_id', '')),
                    rp_id=cred.get('rp_id', ''),
                    rp_name=cred.get('rp_name', ''),
                    user_id=cred.get('user_id', ''),
                    user_name=cred.get('user_name', ''),
                    user_display_name=cred.get('user_display_name', ''),
                    created=0,
                    is_resident=True,
                    algorithm="ES256",
                    cred_id=cred.get('cred_id')
                ))
            self.credentials_loaded.emit(cred_objects)
        
        self._device_manager.get_credentials(pin_to_use, on_loaded, operation_id=self._current_op_id)

    def delete_credential(self, credential: Fido2Credential, pin: Optional[str] = None) -> None:
        """Delete a FIDO2 credential from the device."""
        pin_to_use = pin or self._pin
        
        def on_deleted(result, error):
            if error:
                self.credential_deleted.emit(False, str(error))
            else:
                self.credential_deleted.emit(True, "")
        
        self._device_manager.delete_credential(
            credential.cred_id, pin_to_use, on_deleted, operation_id="fido2_delete"
        )

    def rename_credential(self, credential: Fido2Credential, new_name: str, 
                         pin: Optional[str] = None) -> None:
        """Rename a FIDO2 credential."""
        pin_to_use = pin or self._pin
        
        def on_renamed(result, error):
            if error:
                self.credential_renamed.emit(False, str(error))
            else:
                self.credential_renamed.emit(True, "")
        
        user_id = credential.user_id
        if isinstance(user_id, str):
            try:
                user_id = bytes.fromhex(user_id)
            except ValueError:
                user_id = user_id.encode()
        
        self._device_manager.rename_credential(
            credential.cred_id, new_name, user_id, pin_to_use, 
            on_renamed, operation_id="fido2_rename"
        )

    def set_new_pin(self, new_pin: str) -> None:
        """Set a new PIN on a device that doesn't have one."""
        if len(new_pin) < 4:
            self.pin_changed.emit(False, "PIN must be at least 4 characters")
            return
        
        def on_set(result, error):
            if error:
                if "PIN_POLICY" in str(error):
                    self.pin_changed.emit(False, "PIN does not meet policy requirements")
                else:
                    self.pin_changed.emit(False, f"Failed to set PIN: {error}")
            else:
                # A freshly set PIN should be re-entered on the next privileged
                # operation so the GUI establishes a clean auth session.
                self._pin = None
                self._device_manager.clear_cached_pin()
                self.pin_changed.emit(True, "")
        
        self._device_manager.set_pin(new_pin, on_set, operation_id="fido2_set_pin")

    def change_pin(self, current_pin: str, new_pin: str) -> None:
        """Change the FIDO2 PIN."""
        if len(new_pin) < 4:
            self.pin_changed.emit(False, "PIN must be at least 4 characters")
            return
        
        def on_changed(result, error):
            if error:
                if "PIN_POLICY" in str(error):
                    self.pin_changed.emit(False, "PIN does not meet policy requirements")
                elif "PIN_INVALID" in str(error):
                    self.pin_changed.emit(False, "Current PIN is incorrect")
                else:
                    self.pin_changed.emit(False, f"Failed to change PIN: {error}")
            else:
                self._pin = new_pin
                self._device_manager.set_cached_pin(new_pin)
                self.pin_changed.emit(True, "")
        
        self._device_manager.change_pin(current_pin, new_pin, on_changed, operation_id="fido2_change_pin")
