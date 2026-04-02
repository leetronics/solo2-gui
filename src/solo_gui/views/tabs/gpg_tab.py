"""OpenPGP tab for SoloKeys GUI."""

import os
import platform
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QMessageBox,
    QProgressBar,
    QFrame,
    QScrollArea,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication

from solo_gui.models.device import SoloDevice, firmware_supports_extended_applets
from solo_gui.workers.gpg_worker import (
    GpgWorker,
    GpgKeyInfo,
    GpgKeySlot,
    PCSC_AVAILABLE,
)


def _is_dark_mode() -> bool:
    """Detect if dark mode is enabled."""
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False
    color_scheme = QGuiApplication.styleHints().colorScheme()
    return color_scheme == Qt.ColorScheme.Dark


def _get_card_colors() -> dict:
    """Get color scheme for cards based on dark/light mode."""
    if _is_dark_mode():
        return {
            'bg': '#2d2d2d',
            'hover': '#3d3d3d',
            'border': '#444',
            'text': '#e0e0e0',
            'secondary_text': '#aaa',
        }
    else:
        return {
            'bg': 'white',
            'hover': '#f9f9f9',
            'border': '#e0e0e0',
            'text': '#222',
            'secondary_text': '#666',
        }


def _get_pcsc_help_text() -> str:
    """Get platform-specific PCSC help text."""
    system = platform.system()
    if system == "Darwin":
        return (
            "⚠️ PCSC is not available. OpenPGP requires:\n"
            "  • PC/SC framework (built-in, usually running)\n"
            "  • If not running, restart the smart card service:\n"
            "    sudo launchctl unload /System/Library/LaunchDaemons/com.apple.ifdreader.plist\n"
            "    sudo launchctl load /System/Library/LaunchDaemons/com.apple.ifdreader.plist"
        )
    elif system == "Windows":
        return (
            "⚠️ PCSC is not available. OpenPGP requires:\n"
            "  • Smart Card service available\n"
            "  • SoloKeys CCID/smartcard reader exposed by Windows\n"
            "  • Working pyscard support inside the app"
        )
    else:
        return (
            "⚠️ PCSC is not available. OpenPGP operations require:\n"
            "  • sudo apt install pcscd  (Debian/Ubuntu)\n"
            "  • sudo dnf install pcsc-lite  (Fedora)\n"
            "  • sudo systemctl start pcscd"
        )


_SLOT_META = {
    GpgKeySlot.SIGN:    ("Sign",    "SIG"),
    GpgKeySlot.DECRYPT: ("Decrypt", "DEC"),
    GpgKeySlot.AUTH:    ("Auth",    "AUT"),
}


class GenerateGpgKeyDialog(QDialog):
    """Dialog to configure and launch key generation for one GPG slot."""

    def __init__(self, parent=None, slot: GpgKeySlot = GpgKeySlot.SIGN):
        super().__init__(parent)
        name, badge = _SLOT_META.get(slot, (str(slot), "??"))
        self.setWindowTitle(f"Generate Key — {name} ({badge})")
        self.setModal(True)
        self._slot = slot

        layout = QVBoxLayout(self)
        form = QFormLayout()

        slot_label = QLabel(f"{name} ({badge})")
        slot_label.setStyleSheet("font-weight: bold;")
        form.addRow("Slot:", slot_label)

        self._algo_combo = QComboBox()
        if slot == GpgKeySlot.DECRYPT:
            self._algo_combo.addItem("Cv25519 / X25519 (Recommended)", "Cv25519")
            self._algo_combo.addItem("NIST P-256", "P-256")
        else:
            self._algo_combo.addItem("Ed25519 (Recommended)", "Ed25519")
            self._algo_combo.addItem("NIST P-256", "P-256")
        form.addRow("Algorithm:", self._algo_combo)

        self._admin_pin_input = QLineEdit()
        self._admin_pin_input.setEchoMode(QLineEdit.Password)
        self._admin_pin_input.setPlaceholderText("default: 12345678")
        form.addRow("Admin PIN:", self._admin_pin_input)

        layout.addLayout(form)

        note = QLabel(
            "<b>Note:</b> Generating a key requires the Admin PIN (PW3). "
            "The default Admin PIN is <b>12345678</b>. "
            "This will overwrite any existing key in the slot."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background: #e8f0fe; border: 1px solid #c5d4f5; "
            "border-radius: 4px; padding: 6px; font-size: 9pt; color: #1a3a6b;"
        )
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self._admin_pin_input.text():
            QMessageBox.warning(self, "Admin PIN Required", "Please enter the Admin PIN.")
            return
        self.accept()

    def get_algo(self) -> str:
        return self._algo_combo.currentData()

    def get_admin_pin(self) -> str:
        return self._admin_pin_input.text()


class GpgPinDialog(QDialog):
    """Dialog for changing a GPG PIN."""

    def __init__(self, parent=None, title: str = "Change PIN",
                 current_label: str = "Current PIN:", new_label: str = "New PIN:",
                 min_len: int = 6):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._min_len = min_len

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._current_input = QLineEdit()
        self._current_input.setEchoMode(QLineEdit.Password)
        form.addRow(current_label, self._current_input)

        self._new_input = QLineEdit()
        self._new_input.setEchoMode(QLineEdit.Password)
        form.addRow(new_label, self._new_input)

        self._confirm_input = QLineEdit()
        self._confirm_input.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm:", self._confirm_input)

        layout.addLayout(form)
        layout.addWidget(QLabel(f"PIN must be at least {min_len} characters."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if self._new_input.text() != self._confirm_input.text():
            QMessageBox.warning(self, "Mismatch", "The new PIN values do not match.")
            return
        if len(self._new_input.text()) < self._min_len:
            QMessageBox.warning(
                self, "Too Short", f"PIN must be at least {self._min_len} characters."
            )
            return
        self.accept()

    def get_current(self) -> str:
        return self._current_input.text()

    def get_new(self) -> str:
        return self._new_input.text()


class KeySlotCard(QFrame):
    """Card widget representing one OpenPGP key slot."""

    generate_requested = Signal(object)  # GpgKeySlot
    export_requested = Signal(object)    # GpgKeySlot

    def __init__(self, slot: GpgKeySlot, parent=None):
        super().__init__(parent)
        self._slot = slot
        name, badge = _SLOT_META.get(slot, (str(slot), "??"))

        colors = _get_card_colors()
        self.setObjectName("KeySlotCard")
        self.setStyleSheet(f"""
            KeySlotCard {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                background-color: {colors['bg']};
            }}
            KeySlotCard:hover {{
                border-color: {colors['border']};
                background-color: {colors['hover']};
            }}
        """)
        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(2)

        # Row 1: header
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        badge_label = QLabel(badge)
        badge_label.setStyleSheet(
            "font-size:10px; color:#fff; background:#555; "
            "padding:1px 6px; border-radius:3px;"
        )
        badge_label.setFixedHeight(18)

        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold; font-size: 11pt;")

        self._btn_generate = QPushButton("Generate Key")
        self._btn_generate.setToolTip("Generate a new key pair in this slot")
        self._btn_generate.setStyleSheet(
            "QPushButton { color: white; background: #2a7ae2; border-radius: 3px; "
            "padding: 2px 8px; } "
            "QPushButton:hover { background: #1a5ab2; } "
            "QPushButton:disabled { background: #aaa; }"
        )
        self._btn_generate.clicked.connect(lambda: self.generate_requested.emit(self._slot))

        self._btn_export = QPushButton("Export Public Key")
        self._btn_export.setToolTip("Show the public key bytes")
        self._btn_export.clicked.connect(lambda: self.export_requested.emit(self._slot))
        self._btn_export.setVisible(False)

        row1.addWidget(badge_label)
        row1.addWidget(name_label)
        row1.addStretch()
        row1.addWidget(self._btn_generate)
        row1.addWidget(self._btn_export)

        # Row 2: status info
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addSpacing(42)

        self._status_label = QLabel("No key")
        self._status_label.setStyleSheet("color: gray; font-size: 10pt;")
        row2.addWidget(self._status_label)
        row2.addStretch()

        outer.addLayout(row1)
        outer.addLayout(row2)

    def update_key_info(self, info: GpgKeyInfo) -> None:
        """Refresh card display from a GpgKeyInfo."""
        colors = _get_card_colors()
        self._btn_export.setVisible(info.has_key)
        if not info.has_key:
            self._status_label.setText("No key")
            self._status_label.setStyleSheet(f"color: {colors['secondary_text']}; font-size: 10pt;")
        else:
            parts = []
            if info.algo:
                parts.append(info.algo)
            if info.fingerprint:
                # Show short fingerprint (last 8 chars)
                parts.append(f"…{info.fingerprint[-8:]}")
            if info.created:
                parts.append(f"created {info.created}")
            self._status_label.setText("  ·  ".join(parts) if parts else "Key present")
            self._status_label.setStyleSheet(f"color: {colors['text']}; font-size: 10pt;")

    def set_controls_enabled(self, enabled: bool) -> None:
        self._btn_generate.setEnabled(enabled)
        self._btn_export.setEnabled(enabled)


class GpgTab(QWidget):
    """OpenPGP tab for managing GPG keys on the SoloKeys device."""

    gpg_availability = Signal(bool)  # emitted once per device connect

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[GpgWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._slot_cards: Dict[GpgKeySlot, KeySlotCard] = {}
        self._last_pubkey_bytes: Optional[bytes] = None
        self._last_pubkey_slot: Optional[GpgKeySlot] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # PCSC not running warning
        self._pcsc_warning_label = QLabel(_get_pcsc_help_text())
        self._pcsc_warning_label.setStyleSheet(
            "background-color: #fff3cd; padding: 10px; border-radius: 5px;"
        )
        self._pcsc_warning_label.setVisible(False)
        layout.addWidget(self._pcsc_warning_label)

        # PCSC library not available warning
        if not PCSC_AVAILABLE:
            lib_warning = QLabel(
                _get_pcsc_help_text() + "\n  • pip install pyscard"
            )
            lib_warning.setStyleSheet(
                "background-color: #fff3cd; padding: 10px; border-radius: 5px;"
            )
            layout.addWidget(lib_warning)

        # Key slot cards in a scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(4)
        vbox.setContentsMargins(0, 0, 4, 0)

        for slot in (GpgKeySlot.SIGN, GpgKeySlot.DECRYPT, GpgKeySlot.AUTH):
            card = KeySlotCard(slot, self)
            card.generate_requested.connect(self._generate_key_for)
            card.export_requested.connect(self._export_pubkey_for)
            self._slot_cards[slot] = card
            vbox.addWidget(card)

        vbox.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # PIN Management group
        pin_group = QGroupBox("PIN Management")
        pin_main = QVBoxLayout(pin_group)

        # User PIN row
        user_row = QHBoxLayout()
        user_row.addWidget(QLabel("User PIN (PW1):"))
        self._user_pin_label = QLabel("Unknown")
        user_row.addWidget(self._user_pin_label)
        self._change_user_pin_btn = QPushButton("Change PIN")
        self._change_user_pin_btn.clicked.connect(self._change_user_pin)
        user_row.addWidget(self._change_user_pin_btn)
        user_row.addStretch()
        pin_main.addLayout(user_row)

        # Admin PIN row
        admin_row = QHBoxLayout()
        admin_row.addWidget(QLabel("Admin PIN (PW3):"))
        self._admin_pin_label = QLabel("Unknown")
        admin_row.addWidget(self._admin_pin_label)
        self._change_admin_pin_btn = QPushButton("Change Admin PIN")
        self._change_admin_pin_btn.clicked.connect(self._change_admin_pin)
        admin_row.addWidget(self._change_admin_pin_btn)
        admin_row.addStretch()
        pin_main.addLayout(admin_row)

        hint = QLabel("Default User PIN: 123456 | Default Admin PIN: 12345678")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        pin_main.addWidget(hint)

        # Danger Zone
        danger_group = QGroupBox("Danger Zone")
        danger_group.setStyleSheet(
            "QGroupBox { border: 2px solid #cc0000; color: #cc0000; }"
        )
        danger_layout = QVBoxLayout(danger_group)
        danger_layout.addWidget(
            QLabel("Factory reset will erase all keys and restore default PINs.")
        )
        self._reset_btn = QPushButton("Reset OpenPGP Applet")
        self._reset_btn.setStyleSheet("color: #cc0000;")
        self._reset_btn.clicked.connect(self._factory_reset)
        danger_layout.addWidget(self._reset_btn)
        pin_main.addWidget(danger_group)

        layout.addWidget(pin_group)

        # Status bar
        status_layout = QHBoxLayout()
        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 0)
        self._status_progress.setVisible(False)
        self._status_progress.setMaximumWidth(120)
        self._status_label = QLabel("Ready")
        status_layout.addWidget(self._status_progress)
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        self._set_controls_enabled(False)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self.gpg_availability.emit(self._should_show_tab())
        self._setup_worker()
        if self._worker:
            self._worker.probe_gpg()

    def clear_device(self) -> None:
        self._device = None
        self._cleanup_worker()
        for slot, card in self._slot_cards.items():
            card.update_key_info(GpgKeyInfo(slot=slot, has_key=False,
                                            fingerprint=None, algo=None, created=None))
        self._user_pin_label.setText("Unknown")
        self._admin_pin_label.setText("Unknown")
        self._status_label.setText("No device connected")
        self._set_controls_enabled(False)
        self._pcsc_warning_label.setVisible(False)

    def _setup_worker(self) -> None:
        if not self._device:
            return
        self._cleanup_worker()
        self._worker_thread = QThread()
        self._worker = GpgWorker(self._device)
        self._worker.moveToThread(self._worker_thread)
        self._worker.gpg_probed.connect(self._on_gpg_probed)
        self._worker.status_loaded.connect(self._on_status_loaded)
        self._worker.key_generated.connect(self._on_key_generated)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.reset_completed.connect(self._on_reset_completed)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        pcsc_ok = enabled and PCSC_AVAILABLE
        for card in self._slot_cards.values():
            card.set_controls_enabled(pcsc_ok)
        self._change_user_pin_btn.setEnabled(pcsc_ok)
        self._change_admin_pin_btn.setEnabled(pcsc_ok)
        self._reset_btn.setEnabled(pcsc_ok)

    def _should_show_tab(self) -> bool:
        if not self._device:
            return False
        if getattr(self._device.mode, "value", None) != "regular":
            return False
        info = self._device.get_info()
        return firmware_supports_extended_applets(info.firmware_version)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._status_progress.setVisible(busy)
        self._status_label.setText(message if busy else "Ready")

    def _reload_status(self) -> None:
        if not self._worker:
            return
        self._set_busy(True, "Loading key status...")
        self._worker.load_status()

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_gpg_probed(self, available: bool) -> None:
        self.gpg_availability.emit(self._should_show_tab())
        if available:
            self._pcsc_warning_label.setVisible(False)
            self._set_controls_enabled(True)
            self._reload_status()
        else:
            self._set_controls_enabled(False)
            self._status_label.setText("OpenPGP applet not available")

    def _on_status_loaded(self, key_infos: list, pw_status: dict) -> None:
        self._set_busy(False)
        for info in key_infos:
            card = self._slot_cards.get(info.slot)
            if card:
                card.update_key_info(info)

        user_retries = pw_status.get("user_pin_retries")
        admin_retries = pw_status.get("admin_pin_retries")
        self._user_pin_label.setText(
            f"{user_retries} retries remaining" if user_retries is not None else "Unknown"
        )
        self._admin_pin_label.setText(
            f"{admin_retries} retries remaining" if admin_retries is not None else "Unknown"
        )

    def _on_key_generated(self, success: bool, error: str, pubkey_bytes: bytes, slot) -> None:
        self._set_busy(False)
        if success:
            self._last_pubkey_bytes = pubkey_bytes
            self._last_pubkey_slot = slot
            if pubkey_bytes:
                self._show_pubkey_dialog(pubkey_bytes, slot)
            else:
                QMessageBox.information(self, "Success", "Key generated successfully")
            self._reload_status()
        else:
            QMessageBox.critical(self, "Error", f"Failed to generate key: {error}")

    def _on_pin_changed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", message or "Operation completed successfully")
            self._reload_status()
        else:
            QMessageBox.critical(self, "Error", message)

    def _on_reset_completed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Reset Complete", message)
            self._reload_status()
        else:
            QMessageBox.critical(self, "Reset Failed", message)

    def _on_error_occurred(self, error: str) -> None:
        self._set_busy(False)
        self._status_label.setText(f"Error: {error}")

    # ------------------------------------------------------------------
    # Slot card action handlers
    # ------------------------------------------------------------------

    def _generate_key_for(self, slot: GpgKeySlot) -> None:
        if not self._worker:
            return
        name, badge = _SLOT_META.get(slot, (str(slot), "??"))
        dialog = GenerateGpgKeyDialog(self, slot)
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, f"Generating key in {name} ({badge}) slot...")
            self._worker.generate_key(slot, dialog.get_algo(), dialog.get_admin_pin())

    def _export_pubkey_for(self, slot: GpgKeySlot) -> None:
        """Show the last known public key for the slot, or reload status first."""
        if self._last_pubkey_bytes and self._last_pubkey_slot == slot:
            self._show_pubkey_dialog(self._last_pubkey_bytes, slot)
        else:
            QMessageBox.information(
                self,
                "Export Public Key",
                "Public key bytes are only available immediately after key generation.\n\n"
                "Use 'gpg --card-status' or 'gpg --export' to export the key via GnuPG.",
            )

    def _change_user_pin(self) -> None:
        if not self._worker:
            return
        dialog = GpgPinDialog(
            self, "Change User PIN",
            current_label="Current PIN:", new_label="New PIN:", min_len=6
        )
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Changing User PIN...")
            self._worker.change_user_pin(dialog.get_current(), dialog.get_new())

    def _change_admin_pin(self) -> None:
        if not self._worker:
            return
        dialog = GpgPinDialog(
            self, "Change Admin PIN",
            current_label="Current Admin PIN:", new_label="New Admin PIN:", min_len=8
        )
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Changing Admin PIN...")
            self._worker.change_admin_pin(dialog.get_current(), dialog.get_new())

    def _factory_reset(self) -> None:
        if not self._worker:
            return
        reply = QMessageBox.warning(
            self,
            "Reset OpenPGP Applet",
            "This will permanently erase all OpenPGP keys and reset the PIN to factory defaults.\n\n"
            "Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            self._set_busy(True, "Resetting OpenPGP applet...")
            self._worker.factory_reset()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_pubkey_dialog(self, pubkey_bytes: bytes, slot: GpgKeySlot) -> None:
        name, badge = _SLOT_META.get(slot, (str(slot), "??"))
        # Try to decode as SubjectPublicKeyInfo (DER)
        try:
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PublicFormat, load_der_public_key
            )
            from cryptography.hazmat.backends import default_backend
            pub = load_der_public_key(pubkey_bytes, backend=default_backend())
            display = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        except Exception:
            # Fallback: hex dump of the raw response
            display = pubkey_bytes.hex()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Public Key — {name} ({badge})")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"Key generated in {name} slot. Raw public key response:"))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(display)
        text.setFontFamily("monospace")
        layout.addWidget(text)
        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(display))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        dlg.exec()
