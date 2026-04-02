"""Advanced Secrets tools tab for SoloKeys GUI."""

from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLineEdit,
    QTextEdit,
    QMessageBox,
    QProgressBar,
)

from solo_gui.models.device import SoloDevice
from solo_gui.workers.totp_worker import Credential, OtherKind, TotpWorker


class SecretsToolsTab(QWidget):
    """Challenge-response and reverse-HOTP utilities."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[TotpWorker] = None
        self._credentials: List[Credential] = []
        self._selected: Optional[Credential] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        hmac_group = QGroupBox("HMAC Challenge-Response")
        hmac_layout = QVBoxLayout(hmac_group)
        hmac_layout.addWidget(QLabel(
            "Use this section with HmacSlot1 / HmacSlot2 credentials for KeepassXC-style challenge response."
        ))
        self._hmac_challenge = QLineEdit()
        self._hmac_challenge.setPlaceholderText("Challenge text")
        self._hmac_slot = QLineEdit("1")
        self._hmac_slot.setPlaceholderText("1 or 2")
        self._hmac_result = QTextEdit()
        self._hmac_result.setReadOnly(True)
        run_hmac = QPushButton("Calculate HMAC")
        run_hmac.clicked.connect(self._run_hmac)
        hmac_row = QHBoxLayout()
        hmac_row.addWidget(QLabel("Slot:"))
        hmac_row.addWidget(self._hmac_slot)
        hmac_row.addWidget(QLabel("Challenge:"))
        hmac_row.addWidget(self._hmac_challenge, 1)
        hmac_row.addWidget(run_hmac)
        hmac_layout.addLayout(hmac_row)
        hmac_layout.addWidget(self._hmac_result)
        layout.addWidget(hmac_group)

        reverse_group = QGroupBox("Reverse HOTP")
        reverse_layout = QVBoxLayout(reverse_group)
        self._reverse_list = QListWidget()
        self._reverse_list.currentItemChanged.connect(self._on_selected)
        reverse_layout.addWidget(self._reverse_list)
        reverse_row = QHBoxLayout()
        self._reverse_code = QLineEdit()
        self._reverse_code.setPlaceholderText("Enter code to verify")
        verify = QPushButton("Verify")
        verify.clicked.connect(self._verify_reverse_hotp)
        reverse_row.addWidget(self._reverse_code, 1)
        reverse_row.addWidget(verify)
        reverse_layout.addLayout(reverse_row)
        layout.addWidget(reverse_group)

        status = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._status = QLabel("No device connected")
        status.addWidget(self._status)
        status.addWidget(self._progress)
        status.addStretch()
        layout.addLayout(status)

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self._worker = TotpWorker(device)
        self._worker.credentials_loaded.connect(self._on_credentials_loaded)
        self._worker.reverse_hotp_verified.connect(self._on_verified)
        self._worker.hmac_calculated.connect(self._on_hmac_calculated)
        self._worker.pin_required.connect(lambda: self._set_busy(False, "Unlock the Secrets app in the OTP tab, then retry."))
        self._worker.touch_required.connect(lambda: self._set_busy(False, "Touch the device, then retry."))
        self._worker.error_occurred.connect(self._on_error)
        self._set_busy(True, "Loading advanced secrets credentials...")
        self._worker.load_credentials()

    def clear_device(self) -> None:
        self._device = None
        self._worker = None
        self._credentials = []
        self._selected = None
        self._reverse_list.clear()
        self._status.setText("No device connected")

    def _set_busy(self, busy: bool, message: str) -> None:
        self._progress.setVisible(busy)
        self._status.setText(message if busy else "Ready")

    def _on_credentials_loaded(self, credentials: List[Credential]) -> None:
        self._set_busy(False, "Ready")
        self._credentials = [cred for cred in credentials if cred.other in (OtherKind.REVERSE_HOTP, OtherKind.HMAC)]
        self._reverse_list.clear()
        for cred in self._credentials:
            if cred.other == OtherKind.REVERSE_HOTP:
                item = QListWidgetItem(cred.name)
                item.setData(32, cred)
                self._reverse_list.addItem(item)

    def _on_selected(self, current, previous) -> None:
        self._selected = current.data(32) if current else None

    def _verify_reverse_hotp(self) -> None:
        if not self._worker or not self._selected or not self._reverse_code.text().strip():
            return
        self._set_busy(True, "Verifying reverse-HOTP code...")
        self._worker.verify_reverse_hotp(self._selected, self._reverse_code.text().strip())

    def _run_hmac(self) -> None:
        if not self._worker:
            return
        try:
            slot = int(self._hmac_slot.text().strip() or "1")
        except ValueError:
            QMessageBox.warning(self, "HMAC", "Slot must be 1 or 2.")
            return
        self._set_busy(True, "Calculating HMAC...")
        self._worker.calculate_hmac(slot, self._hmac_challenge.text().encode("utf-8"))

    def _on_verified(self, success: bool, message: str) -> None:
        self._set_busy(False, "Ready")
        if success:
            QMessageBox.information(self, "Reverse HOTP", message)
        else:
            QMessageBox.warning(self, "Reverse HOTP", message)

    def _on_hmac_calculated(self, result: str) -> None:
        self._set_busy(False, "Ready")
        self._hmac_result.setPlainText(result)

    def _on_error(self, error: str) -> None:
        self._set_busy(False, error)
        QMessageBox.warning(self, "Secrets Tools", error)
