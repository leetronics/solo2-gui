"""HMAC tab for SoloKeys GUI."""

from __future__ import annotations

import base64
from typing import Optional

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
)

from solo_gui.models.device import SoloDevice, firmware_supports_extended_applets
from solo_gui.workers.totp_worker import (
    HMAC_SLOT_NAMES,
    HMAC_SLOT_NUMBERS,
    HmacSlotInfo,
    TotpWorker,
    normalize_hmac_secret,
)


class GeneratedSecretDialog(QDialog):
    """Show a newly generated HMAC secret before programming it onto the token."""

    def __init__(self, slot_name: str, secret: bytes, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Generated Secret for {slot_name}")
        self.resize(560, 300)

        secret_hex = secret.hex()
        secret_b32 = base64.b32encode(secret).decode("ascii")

        layout = QVBoxLayout(self)
        info = QLabel(
            f"Save this secret before programming {slot_name}. "
            "You need it again to provision a backup device."
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Program Slot")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HmacTab(QWidget):
    """HMAC challenge-response slot management."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[TotpWorker] = None
        self._hmac_slots = self._blank_slots()
        self._slot_widgets: dict[int, dict[str, QWidget]] = {}
        self._setup_ui()

    def _blank_slots(self) -> dict[int, HmacSlotInfo]:
        return {
            slot: HmacSlotInfo(slot=slot, name=HMAC_SLOT_NAMES[slot], configured=False)
            for slot in HMAC_SLOT_NUMBERS
        }

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        hmac_group = QGroupBox("HMAC Challenge-Response")
        hmac_layout = QVBoxLayout(hmac_group)
        intro = QLabel(
            "Configure HMAC-SHA1 challenge-response secrets in HmacSlot1 or HmacSlot2. "
            "Some software, including KeePassXC, uses Slot 2 by default, but both slots are supported."
        )
        intro.setWordWrap(True)
        hmac_layout.addWidget(intro)

        for slot in HMAC_SLOT_NUMBERS:
            self._add_slot_group(hmac_layout, slot)

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

    def _add_slot_group(self, parent_layout: QVBoxLayout, slot: int) -> None:
        group = QGroupBox(HMAC_SLOT_NAMES[slot])
        group_layout = QVBoxLayout(group)

        slot_row = QHBoxLayout()
        state = QLabel("No device connected")
        slot_row.addWidget(QLabel("Status:"))
        slot_row.addWidget(state)
        slot_row.addStretch()
        group_layout.addLayout(slot_row)

        button_row = QHBoxLayout()
        generate_btn = QPushButton("Generate Secret")
        generate_btn.clicked.connect(
            lambda _checked=False, current_slot=slot: self._generate_hmac_secret(current_slot)
        )
        button_row.addWidget(generate_btn)
        import_btn = QPushButton("Import Secret")
        import_btn.clicked.connect(
            lambda _checked=False, current_slot=slot: self._import_hmac_secret(current_slot)
        )
        button_row.addWidget(import_btn)
        remove_btn = QPushButton("Remove Slot")
        remove_btn.clicked.connect(
            lambda _checked=False, current_slot=slot: self._remove_hmac_slot(current_slot)
        )
        button_row.addWidget(remove_btn)
        button_row.addStretch()
        group_layout.addLayout(button_row)

        self._slot_widgets[slot] = {
            "state": state,
            "generate": generate_btn,
            "import": import_btn,
            "remove": remove_btn,
        }
        parent_layout.addWidget(group)

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        if getattr(device.mode, "value", None) != "regular":
            self._worker = None
            self._hmac_slots = self._blank_slots()
            self._refresh_hmac_controls()
            self._status.setText("HMAC unavailable in bootloader mode")
            return
        info = device.get_info()
        if not firmware_supports_extended_applets(info.firmware_version):
            self._worker = None
            self._hmac_slots = self._blank_slots()
            self._refresh_hmac_controls()
            self._status.setText("HMAC unavailable on this firmware")
            return
        self._worker = TotpWorker(device)
        self._worker.hmac_slots_loaded.connect(self._on_hmac_slots_loaded)
        self._worker.hmac_slot_configured.connect(self._on_hmac_slot_configured)
        self._worker.hmac_slot_removed.connect(self._on_hmac_slot_removed)
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
        self._hmac_slots = self._blank_slots()
        self._refresh_hmac_controls()
        self._status.setText("No device connected")

    def _set_busy(self, busy: bool, message: str) -> None:
        self._progress.setVisible(busy)
        self._status.setText(message)

    def _refresh_hmac_controls(self) -> None:
        worker_ready = self._worker is not None
        for slot, widgets in self._slot_widgets.items():
            slot_info = self._hmac_slots[slot]
            state = widgets["state"]
            generate_btn = widgets["generate"]
            import_btn = widgets["import"]
            remove_btn = widgets["remove"]

            if not worker_ready:
                state.setText("No device connected")
                state.setStyleSheet("color: #777; font-weight: bold;")
            elif slot_info.configured:
                state.setText("Configured")
                state.setStyleSheet("color: green; font-weight: bold;")
            else:
                state.setText("Not configured")
                state.setStyleSheet("color: #c77d00; font-weight: bold;")

            generate_btn.setEnabled(worker_ready)
            generate_btn.setText("Replace Secret" if slot_info.configured else "Generate Secret")
            import_btn.setEnabled(worker_ready)
            import_btn.setText("Replace via Import" if slot_info.configured else "Import Secret")
            remove_btn.setEnabled(worker_ready and slot_info.configured)

    def _on_hmac_slots_loaded(self, slots: list[HmacSlotInfo]) -> None:
        self._set_busy(False, "Ready")
        self._hmac_slots = self._blank_slots()
        for slot_info in slots:
            self._hmac_slots[slot_info.slot] = slot_info
        self._refresh_hmac_controls()

    def _confirm_hmac_replacement(self, slot: int) -> bool:
        if not self._hmac_slots[slot].configured:
            return True
        answer = QMessageBox.question(
            self,
            "Replace HMAC Secret",
            f"{HMAC_SLOT_NAMES[slot]} is already configured.\n\nReplace the existing secret?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _generate_hmac_secret(self, slot: int) -> None:
        if not self._worker or not self._confirm_hmac_replacement(slot):
            return
        secret = self._worker.generate_hmac_secret()
        dialog = GeneratedSecretDialog(HMAC_SLOT_NAMES[slot], secret, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._set_busy(True, f"Programming {HMAC_SLOT_NAMES[slot]}...")
        self._worker.configure_hmac_slot(slot, secret, overwrite=self._hmac_slots[slot].configured)

    def _import_hmac_secret(self, slot: int) -> None:
        if not self._worker or not self._confirm_hmac_replacement(slot):
            return
        secret_text, accepted = QInputDialog.getText(
            self,
            "Import HMAC Secret",
            "Enter a 20-byte HMAC secret in hex or base32 format:",
        )
        if not accepted or not secret_text.strip():
            return
        try:
            normalize_hmac_secret(secret_text)
        except Exception as exc:
            QMessageBox.warning(self, "Import Secret", str(exc))
            return
        self._set_busy(True, f"Programming {HMAC_SLOT_NAMES[slot]}...")
        self._worker.configure_hmac_slot(
            slot,
            secret_text,
            overwrite=self._hmac_slots[slot].configured,
        )

    def _remove_hmac_slot(self, slot: int) -> None:
        if not self._worker or not self._hmac_slots[slot].configured:
            return
        answer = QMessageBox.question(
            self,
            "Remove HMAC Secret",
            f"Remove the configured secret from {HMAC_SLOT_NAMES[slot]}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True, f"Removing {HMAC_SLOT_NAMES[slot]}...")
        self._worker.delete_hmac_slot(slot)

    def _on_hmac_slot_configured(self, success: bool, error: str, slot_info: object) -> None:
        self._set_busy(False, "Ready")
        if not success or not isinstance(slot_info, HmacSlotInfo):
            QMessageBox.warning(self, "HMAC", error or "Failed to configure HMAC slot.")
            return
        self._hmac_slots[slot_info.slot] = slot_info
        self._refresh_hmac_controls()
        QMessageBox.information(self, "HMAC", f"Configured {slot_info.name} successfully.")

    def _on_hmac_slot_removed(self, success: bool, error: str, slot: int) -> None:
        self._set_busy(False, "Ready")
        if not success:
            QMessageBox.warning(self, "HMAC", error or "Failed to remove HMAC slot.")
            return
        self._hmac_slots[slot] = HmacSlotInfo(
            slot=slot,
            name=HMAC_SLOT_NAMES[slot],
            configured=False,
        )
        self._refresh_hmac_controls()
        QMessageBox.information(self, "HMAC", f"Removed {HMAC_SLOT_NAMES[slot]}.")

    def _on_error(self, error: str) -> None:
        self._set_busy(False, error)
        QMessageBox.warning(self, "HMAC", error)
