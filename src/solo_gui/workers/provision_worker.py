"""Provision app worker for SoloKeys GUI using the standalone solo2 package."""

from PySide6.QtCore import QObject, Signal

from solo2.provisioner import ProvisionerSession


class ProvisionWorker(QObject):
    """Worker for Solo 2 Provision app operations (Hacker variant only)."""

    operation_completed = Signal(bool, str)
    keypair_generated = Signal(str, bytes)
    error_occurred = Signal(str)

    def __init__(self, device):
        super().__init__()
        self._device = device

    def _session(self) -> ProvisionerSession:
        return ProvisionerSession(self._device)

    def generate_key(self, key_type: str) -> None:
        try:
            result = self._session().generate_key(key_type)
            self.keypair_generated.emit(result.key_type, result.public_key)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def store_certificate(self, key_type: str, der_data: bytes) -> None:
        try:
            self._session().store_certificate(key_type, der_data)
            self.operation_completed.emit(True, f"{key_type} certificate stored successfully")
        except Exception as exc:
            self.operation_completed.emit(False, str(exc))

    def store_t1_pubkey(self, pubkey_bytes: bytes) -> None:
        try:
            self._session().store_t1_pubkey(pubkey_bytes)
            self.operation_completed.emit(True, "T1 public key stored successfully")
        except Exception as exc:
            self.operation_completed.emit(False, str(exc))

    def reformat_filesystem(self) -> None:
        try:
            self._session().reformat_filesystem()
            self.operation_completed.emit(True, "Filesystem reformatted successfully")
        except Exception as exc:
            self.operation_completed.emit(False, str(exc))

    def write_file(self, path: str, data: bytes) -> None:
        try:
            self._session().write_file(path, data)
            self.operation_completed.emit(True, f"File written: {path}")
        except Exception as exc:
            self.operation_completed.emit(False, str(exc))
