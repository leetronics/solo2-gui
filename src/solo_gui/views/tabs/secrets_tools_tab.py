"""KeePassXC HMAC tab for SoloKeys GUI."""

from __future__ import annotations

import base64
from typing import List, Optional

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from solo_gui.models.device import SoloDevice
from solo_gui.workers.totp_worker import (
    HmacSlotInfo,
    KEEPASSXC_HMAC_NAME,
    KEEPASSXC_HMAC_SLOT,
    TotpWorker,
    normalize_hmac_secret,
)


class GeneratedSecretDialog(QDialog):
    """Show a newly generated HMAC secret before programming it onto the token."""

    def __init__(self, secret: bytes, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Generated KeePassXC Secret")
        self.resize(560, 300)

        secret_hex = secret.hex()
        secret_b32 = base64.b32encode(secret).decode("ascii")

        layout = QVBoxLayout(self)
        info = QLabel(
            "Save this secret before programming the slot. You need it again to provision a backup device."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        hex_label = QLabel("Hex")
        hex_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(hex_label)
        self._hex_value = QTextEdit(secret_hex)
        self._hex_value.setReadOnly(True)
        self._hex_value.setFixedHeight(72)
        layout.addWidget(self._hex_value)

        b32_label = QLabel("Base32")
        b32_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(b32_label)
        self._b32_value = QTextEdit(secret_b32)
        self._b32_value.setReadOnly(True)
        self._b32_value.setFixedHeight(72)
        layout.addWidget(self._b32_value)

        copy_row = QHBoxLayout()
        copy_hex = QPushButton("Copy Hex")
        copy_hex.clicked.connect(lambda: QApplication.clipboard().setText(secret_hex))
        copy_row.addWidget(copy_hex)
        copy_b32 = QPushButton("Copy Base32")
        copy_b32.clicked.connect(lambda: QApplication.clipboard().setText(secret_b32))
        copy_row.addWidget(copy_b32)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Program Slot")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HmacTab(QWidget):
    """KeePassXC-compatible HMAC challenge-response utilities."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[TotpWorker] = None
        self._hmac_slot: HmacSlotInfo = HmacSlotInfo(
            slot=KEEPASSXC_HMAC_SLOT,
            name=KEEPASSXC_HMAC_NAME,
            configured=False,
        )
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        hmac_group = QGroupBox("KeePassXC Challenge-Response")
        hmac_layout = QVBoxLayout(hmac_group)
        intro = QLabel(
            "KeePassXC expects a KeePass-compatible HMAC-SHA1 secret stored as HmacSlot2. "
            "Use Generate or Import to provision the slot, then use Test to verify challenge-response output."
        )
        intro.setWordWrap(True)
        hmac_layout.addWidget(intro)

        slot_row = QHBoxLayout()
        self._hmac_name = QLabel(KEEPASSXC_HMAC_NAME)
        self._hmac_name.setStyleSheet("font-weight: bold;")
        self._hmac_state = QLabel("No device connected")
        slot_row.addWidget(QLabel("Slot:"))
        slot_row.addWidget(self._hmac_name)
        slot_row.addStretch()
        slot_row.addWidget(self._hmac_state)
        hmac_layout.addLayout(slot_row)

        button_row = QHBoxLayout()
        self._generate_hmac_btn = QPushButton("Generate Secret")
        self._generate_hmac_btn.clicked.connect(self._generate_hmac_secret)
        button_row.addWidget(self._generate_hmac_btn)
        self._import_hmac_btn = QPushButton("Import Secret")
        self._import_hmac_btn.clicked.connect(self._import_hmac_secret)
        button_row.addWidget(self._import_hmac_btn)
        self._remove_hmac_btn = QPushButton("Remove Slot")
        self._remove_hmac_btn.clicked.connect(self._remove_hmac_slot)
        button_row.addWidget(self._remove_hmac_btn)
        button_row.addStretch()
        hmac_layout.addLayout(button_row)

        test_row = QHBoxLayout()
        self._hmac_challenge = QLineEdit()
        self._hmac_challenge.setPlaceholderText("Challenge text")
        test_row.addWidget(QLabel("Test challenge:"))
        test_row.addWidget(self._hmac_challenge, 1)
        self._test_hmac_btn = QPushButton("Test")
        self._test_hmac_btn.clicked.connect(self._run_hmac)
        test_row.addWidget(self._test_hmac_btn)
        hmac_layout.addLayout(test_row)

        self._hmac_result = QTextEdit()
        self._hmac_result.setReadOnly(True)
        self._hmac_result.setPlaceholderText("HMAC result will appear here")
        self._hmac_result.setFixedHeight(90)
        hmac_layout.addWidget(self._hmac_result)
        layout.addWidget(hmac_group)

        status = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._status = QLabel("No device connected")
        status.addWidget(self._status)
        status.addWidget(self._progress)
        status.addStretch()
        layout.addLayout(status)

        self._refresh_hmac_controls()

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        if getattr(device.mode, "value", None) != "regular":
            self._worker = None
            self._hmac_result.clear()
            self._hmac_slot = HmacSlotInfo(
                slot=KEEPASSXC_HMAC_SLOT,
                name=KEEPASSXC_HMAC_NAME,
                configured=False,
            )
            self._refresh_hmac_controls()
            self._status.setText("HMAC unavailable in bootloader mode")
            return
        self._worker = TotpWorker(device)
        self._worker.hmac_slots_loaded.connect(self._on_hmac_slots_loaded)
        self._worker.hmac_slot_configured.connect(self._on_hmac_slot_configured)
        self._worker.hmac_slot_removed.connect(self._on_hmac_slot_removed)
        self._worker.hmac_calculated.connect(self._on_hmac_calculated)
        self._worker.pin_required.connect(
            lambda: self._set_busy(False, "Unlock Vault in the Credentials tab, then retry.")
        )
        self._worker.touch_required.connect(
            lambda: self._set_busy(False, "Touch the device, then retry.")
        )
        self._worker.error_occurred.connect(self._on_error)
        self._set_busy(True, "Loading HMAC slot status...")
        self._worker.load_hmac_slots()

    def clear_device(self) -> None:
        self._device = None
        self._worker = None
        self._hmac_result.clear()
        self._hmac_slot = HmacSlotInfo(
            slot=KEEPASSXC_HMAC_SLOT,
            name=KEEPASSXC_HMAC_NAME,
            configured=False,
        )
        self._refresh_hmac_controls()
        self._status.setText("No device connected")

    def _set_busy(self, busy: bool, message: str) -> None:
        self._progress.setVisible(busy)
        self._status.setText(message)

    def _refresh_hmac_controls(self) -> None:
        worker_ready = self._worker is not None
        configured = self._hmac_slot.configured
        if not worker_ready:
            self._hmac_state.setText("No device connected")
            self._hmac_state.setStyleSheet("color: #777; font-weight: bold;")
        elif configured:
            self._hmac_state.setText("Configured")
            self._hmac_state.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._hmac_state.setText("Not configured")
            self._hmac_state.setStyleSheet("color: #c77d00; font-weight: bold;")

        self._generate_hmac_btn.setEnabled(worker_ready)
        self._generate_hmac_btn.setText("Replace Secret" if configured else "Generate Secret")
        self._import_hmac_btn.setEnabled(worker_ready)
        self._import_hmac_btn.setText("Replace via Import" if configured else "Import Secret")
        self._remove_hmac_btn.setEnabled(worker_ready and configured)
        self._test_hmac_btn.setEnabled(worker_ready and configured)
        self._hmac_challenge.setEnabled(worker_ready and configured)

    def _on_hmac_slots_loaded(self, slots: List[HmacSlotInfo]) -> None:
        self._set_busy(False, "Ready")
        self._hmac_slot = next(
            (slot for slot in slots if slot.slot == KEEPASSXC_HMAC_SLOT),
            HmacSlotInfo(slot=KEEPASSXC_HMAC_SLOT, name=KEEPASSXC_HMAC_NAME, configured=False),
        )
        self._refresh_hmac_controls()

    def _confirm_hmac_replacement(self) -> bool:
        if not self._hmac_slot.configured:
            return True
        answer = QMessageBox.question(
            self,
            "Replace KeePassXC Secret",
            "HmacSlot2 is already configured.\n\nReplace the existing secret?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _generate_hmac_secret(self) -> None:
        if not self._worker or not self._confirm_hmac_replacement():
            return
        secret = self._worker.generate_hmac_secret()
        dialog = GeneratedSecretDialog(secret, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._set_busy(True, "Programming KeePassXC HMAC slot...")
        self._worker.configure_hmac_slot(secret, overwrite=self._hmac_slot.configured)

    def _import_hmac_secret(self) -> None:
        if not self._worker or not self._confirm_hmac_replacement():
            return
        secret_text, accepted = QInputDialog.getText(
            self,
            "Import KeePassXC Secret",
            "Enter a 20-byte HMAC secret in hex or base32 format:",
        )
        if not accepted or not secret_text.strip():
            return
        try:
            normalize_hmac_secret(secret_text)
        except Exception as exc:
            QMessageBox.warning(self, "Import Secret", str(exc))
            return
        self._set_busy(True, "Programming KeePassXC HMAC slot...")
        self._worker.configure_hmac_slot(secret_text, overwrite=self._hmac_slot.configured)

    def _remove_hmac_slot(self) -> None:
        if not self._worker or not self._hmac_slot.configured:
            return
        answer = QMessageBox.question(
            self,
            "Remove KeePassXC Secret",
            "Remove the configured HmacSlot2 secret?\n\nKeePassXC challenge-response will stop working until you configure a new secret.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True, "Removing KeePassXC HMAC slot...")
        self._worker.delete_hmac_slot()

    def _run_hmac(self) -> None:
        if not self._worker or not self._hmac_slot.configured:
            return
        challenge = self._hmac_challenge.text().strip()
        if not challenge:
            QMessageBox.warning(self, "KeePassXC HMAC", "Enter a challenge value first.")
            return
        self._set_busy(True, "Calculating HMAC...")
        self._worker.calculate_hmac(KEEPASSXC_HMAC_SLOT, challenge.encode("utf-8"))

    def _on_hmac_slot_configured(self, success: bool, error: str, slot_info: object) -> None:
        self._set_busy(False, "Ready")
        if not success or not isinstance(slot_info, HmacSlotInfo):
            QMessageBox.warning(self, "KeePassXC HMAC", error or "Failed to configure HmacSlot2.")
            return
        self._hmac_slot = slot_info
        self._refresh_hmac_controls()
        QMessageBox.information(
            self,
            "KeePassXC HMAC",
            "Configured HmacSlot2 successfully.\n\nKeePassXC should now detect the challenge-response slot.",
        )

    def _on_hmac_slot_removed(self, success: bool, error: str) -> None:
        self._set_busy(False, "Ready")
        if not success:
            QMessageBox.warning(self, "KeePassXC HMAC", error or "Failed to remove HmacSlot2.")
            return
        self._hmac_slot = HmacSlotInfo(
            slot=KEEPASSXC_HMAC_SLOT,
            name=KEEPASSXC_HMAC_NAME,
            configured=False,
        )
        self._refresh_hmac_controls()
        self._hmac_result.clear()
        QMessageBox.information(self, "KeePassXC HMAC", "Removed HmacSlot2.")

    def _on_hmac_calculated(self, result: str) -> None:
        self._set_busy(False, "Ready")
        self._hmac_result.setPlainText(result)

    def _on_error(self, error: str) -> None:
        self._set_busy(False, error)
        QMessageBox.warning(self, "KeePassXC HMAC", error)
