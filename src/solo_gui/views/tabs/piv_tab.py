"""PIV tab for SoloKeys GUI."""

import re
import sys
import platform
import os
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
    QFileDialog,
    QTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication


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


def _get_warning_colors() -> dict:
    """Get theme-aware colors for warning banners."""
    if _is_dark_mode():
        return {
            'bg': '#4a3b12',
            'border': '#8a6d1f',
            'text': '#f3e3a1',
        }
    return {
        'bg': '#fff3cd',
        'border': '#e0c36d',
        'text': '#664d03',
    }


def _get_action_button_colors() -> dict:
    """Get theme-aware colors for slot action buttons."""
    if _is_dark_mode():
        return {
            'primary_bg': '#2a7ae2',
            'primary_hover': '#1a5ab2',
            'primary_disabled': '#475569',
            'primary_text': '#ffffff',
            'secondary_bg': '#3a3a3a',
            'secondary_hover': '#474747',
            'secondary_disabled_bg': '#2f2f2f',
            'secondary_border': '#5a5a5a',
            'secondary_disabled_border': '#474747',
            'secondary_text': '#e0e0e0',
            'secondary_disabled_text': '#888888',
        }
    return {
        'primary_bg': '#2a7ae2',
        'primary_hover': '#1a5ab2',
        'primary_disabled': '#9cbbe9',
        'primary_text': '#ffffff',
        'secondary_bg': '#ffffff',
        'secondary_hover': '#f5f7fb',
        'secondary_disabled_bg': '#f3f3f3',
        'secondary_border': '#c6ccd5',
        'secondary_disabled_border': '#d8d8d8',
        'secondary_text': '#1f2937',
        'secondary_disabled_text': '#8a8a8a',
    }


def _get_danger_zone_colors() -> dict:
    """Get theme-aware colors for destructive action groups."""
    if _is_dark_mode():
        return {
            'bg': '#342629',
            'border': '#8f4d55',
            'title_bg': '#442d31',
            'title_text': '#ffb8bf',
            'text': '#f0dadd',
            'button_hover': '#463136',
            'button_disabled_border': '#66545a',
            'button_disabled_text': '#8b7b80',
        }
    return {
        'bg': '#fff7f8',
        'border': '#e0b6bb',
        'title_bg': '#fff0f2',
        'title_text': '#a23a44',
        'text': '#6f2c33',
        'button_hover': '#fff1f3',
        'button_disabled_border': '#d8c2c5',
        'button_disabled_text': '#a79698',
    }

from solo_gui.models.device import SoloDevice
from solo_gui.models.device import firmware_supports_extended_applets
from solo_gui.workers.piv_worker import (
    PivWorker,
    PivCertificate,
    PivSlot,
    PivKeyType,
    SlotInfo,
    PCSC_AVAILABLE,
    DEFAULT_MANAGEMENT_KEY,
)


def _get_pcsc_help_text() -> str:
    """Get platform-specific PCSC help text."""
    system = platform.system()
    if system == "Darwin":  # macOS
        return (
            "⚠️ PCSC is not available. On macOS, PIV requires:\n"
            "  • PC/SC framework (built-in, usually running)\n"
            "  • If not running, restart the smart card service:\n"
            "    sudo launchctl unload /System/Library/LaunchDaemons/com.apple.ifdreader.plist\n"
            "    sudo launchctl load /System/Library/LaunchDaemons/com.apple.ifdreader.plist"
        )
    elif system == "Windows":
        return (
            "⚠️ PCSC is not available. On Windows, PIV requires:\n"
            "  • Smart Card service available\n"
            "  • SoloKeys CCID/smartcard reader exposed by Windows\n"
            "  • Working pyscard support inside the app"
        )
    else:  # Linux and others
        return (
            "⚠️ PCSC is not available. PIV operations require:\n"
            "  • sudo apt install pcscd  (Debian/Ubuntu)\n"
            "  • sudo dnf install pcsc-lite  (Fedora)\n"
            "  • sudo systemctl start pcscd"
        )

# Human-readable metadata per slot
_SLOT_META = {
    PivSlot.AUTHENTICATION: ("Authentication", "9A"),
    PivSlot.SIGNATURE: ("Digital Signature", "9C"),
    PivSlot.KEY_MANAGEMENT: ("Key Management", "9D"),
    PivSlot.CARD_AUTH: ("Card Authentication", "9E"),
}

_KEY_TYPE_LABELS = {
    PivKeyType.ECC_P256: "ECC P-256",
    PivKeyType.ECC_P384: "ECC P-384",
    PivKeyType.RSA_2048: "RSA 2048",
}


class PivPinDialog(QDialog):
    """Dialog for PIV PIN operations."""

    def __init__(
        self,
        parent=None,
        title="Enter PIN",
        show_current=True,
        show_new=False,
        current_label="PIN:",
        new_label="New PIN:",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self._current_input = None
        self._new_input = None
        self._confirm_input = None

        if show_current:
            self._current_input = QLineEdit()
            self._current_input.setEchoMode(QLineEdit.Password)
            form_layout.addRow(current_label, self._current_input)

        if show_new:
            self._new_input = QLineEdit()
            self._new_input.setEchoMode(QLineEdit.Password)
            form_layout.addRow(new_label, self._new_input)

            self._confirm_input = QLineEdit()
            self._confirm_input.setEchoMode(QLineEdit.Password)
            form_layout.addRow("Confirm:", self._confirm_input)

            layout.addWidget(QLabel("PIN/PUK must be 6-8 characters."))

        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        if self._new_input and self._confirm_input:
            if self._new_input.text() != self._confirm_input.text():
                QMessageBox.warning(self, "Mismatch", "The values do not match.")
                return
            if len(self._new_input.text()) < 6 or len(self._new_input.text()) > 8:
                QMessageBox.warning(
                    self, "Invalid Length", "PIN/PUK must be 6-8 characters."
                )
                return
        self.accept()

    def get_current(self) -> Optional[str]:
        return self._current_input.text() if self._current_input else None

    def get_new(self) -> Optional[str]:
        return self._new_input.text() if self._new_input else None


class GenerateKeyDialog(QDialog):
    """Dialog for generating a new PIV key in a specific slot."""

    def __init__(self, parent=None, slot: PivSlot = PivSlot.AUTHENTICATION):
        super().__init__(parent)
        name, hex_id = _SLOT_META.get(slot, (str(slot), "??"))
        self.setWindowTitle(f"Generate Key — {name} ({hex_id})")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Slot info (read-only label)
        slot_label = QLabel(f"{name} ({hex_id})")
        slot_label.setStyleSheet("font-weight: bold;")
        form_layout.addRow("Slot:", slot_label)

        # Algorithm selection
        self._algo_combo = QComboBox()
        self._algo_combo.addItem("ECC P-256 (Recommended)", PivKeyType.ECC_P256)
        self._algo_combo.addItem("ECC P-384", PivKeyType.ECC_P384)
        form_layout.addRow("Algorithm:", self._algo_combo)

        # PIN input
        self._pin_input = QLineEdit()
        self._pin_input.setEchoMode(QLineEdit.Password)
        self._pin_input.setPlaceholderText("default: 123456")
        form_layout.addRow("PIN:", self._pin_input)

        # Management key input
        self._mgmt_key_input = QLineEdit()
        self._mgmt_key_input.setText(DEFAULT_MANAGEMENT_KEY)
        self._mgmt_key_input.setPlaceholderText("24-byte hex management key")
        form_layout.addRow("Management Key:", self._mgmt_key_input)

        layout.addLayout(form_layout)

        workflow_note = QLabel(
            "<b>Typical workflow:</b> Generate the private key on the device → "
            "use the public key shown after generation to create a CSR or request a certificate → "
            "use <i>Import Cert</i> to store the issued certificate.<br>"
            "The slot is only fully ready for normal PIV use once a matching certificate has been imported."
        )
        workflow_note.setWordWrap(True)
        workflow_note.setStyleSheet(
            "background: #e8f0fe; border: 1px solid #c5d4f5; "
            "border-radius: 4px; padding: 6px; font-size: 9pt; color: #1a3a6b;"
        )
        layout.addWidget(workflow_note)

        layout.addWidget(
            QLabel("Warning: This will overwrite any existing key and certificate in the slot.")
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_key_type(self) -> PivKeyType:
        return self._algo_combo.currentData()

    def get_pin(self) -> str:
        return self._pin_input.text()

    def get_mgmt_key(self) -> str:
        return self._mgmt_key_input.text()


class SlotCard(QFrame):
    """Card widget representing one PIV slot."""

    generate_requested = Signal(object)     # PivSlot
    import_cert_requested = Signal(object)  # PivSlot
    export_cert_requested = Signal(object)  # PivSlot
    delete_requested = Signal(object)       # PivSlot

    def __init__(self, slot: PivSlot, parent=None):
        super().__init__(parent)
        self._slot = slot
        self._has_key = False
        self._has_cert = False
        name, hex_id = _SLOT_META.get(slot, (str(slot), "??"))

        colors = _get_card_colors()
        self.setObjectName("SlotCard")
        self.setStyleSheet(f"""
            SlotCard {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                background-color: {colors['bg']};
            }}
            SlotCard:hover {{
                border-color: {colors['border']};
                background-color: {colors['hover']};
            }}
        """)
        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(2)

        # --- Row 1: header ---
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        badge = QLabel(hex_id)
        badge.setStyleSheet(
            "font-size:10px; color:#fff; background:#555; "
            "padding:1px 6px; border-radius:3px;"
        )
        badge.setFixedHeight(18)

        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold; font-size: 11pt;")

        self._status_label = QLabel("- empty")
        self._status_label.setStyleSheet(
            f"color: {colors['secondary_text']}; font-size: 10pt;"
        )

        self._btn_generate = QPushButton("Generate Key")
        self._btn_generate.setToolTip("Generate a new key pair")
        self._btn_generate.clicked.connect(lambda: self.generate_requested.emit(self._slot))

        self._btn_import = QPushButton("Import Cert")
        self._btn_import.setToolTip("Import an X.509 certificate")
        self._btn_import.clicked.connect(lambda: self.import_cert_requested.emit(self._slot))

        self._btn_export = QPushButton("Export Cert")
        self._btn_export.setToolTip("Export certificate to file")
        self._btn_export.clicked.connect(lambda: self.export_cert_requested.emit(self._slot))
        self._btn_export.setVisible(False)

        self._btn_delete = QPushButton("✕")
        self._btn_delete.setToolTip("Delete certificate")
        self._btn_delete.setStyleSheet(
            "QPushButton { color: #cc0000; border: 1px solid #cc0000; "
            "border-radius: 3px; padding: 2px 6px; } "
            "QPushButton:hover { background: #fff0f0; }"
        )
        self._btn_delete.clicked.connect(lambda: self.delete_requested.emit(self._slot))
        self._btn_delete.setVisible(False)

        row1.addWidget(badge)
        row1.addWidget(name_label)
        row1.addWidget(self._status_label)
        row1.addStretch()
        row1.addWidget(self._btn_generate)
        row1.addWidget(self._btn_import)
        row1.addWidget(self._btn_export)
        row1.addWidget(self._btn_delete)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addSpacing(42)

        self._hint_label = QLabel("Generate a new device-resident key in this slot.")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(
            f"color: {colors['secondary_text']}; font-size: 9pt;"
        )
        row2.addWidget(self._hint_label)
        row2.addStretch()

        outer.addLayout(row1)
        outer.addLayout(row2)
        self._apply_action_styles()

    def _button_style(self, primary: bool) -> str:
        colors = _get_action_button_colors()
        if primary:
            return (
                "QPushButton { "
                f"color: {colors['primary_text']}; "
                f"background: {colors['primary_bg']}; "
                f"border: 1px solid {colors['primary_bg']}; "
                "border-radius: 3px; padding: 2px 8px; font-weight: 600; } "
                "QPushButton:hover { "
                f"background: {colors['primary_hover']}; border-color: {colors['primary_hover']}; }} "
                "QPushButton:disabled { "
                f"background: {colors['primary_disabled']}; border-color: {colors['primary_disabled']}; "
                f"color: {colors['primary_text']}; }}"
            )
        return (
            "QPushButton { "
            f"color: {colors['secondary_text']}; "
            f"background: {colors['secondary_bg']}; "
            f"border: 1px solid {colors['secondary_border']}; "
            "border-radius: 3px; padding: 2px 8px; } "
            "QPushButton:hover { "
            f"background: {colors['secondary_hover']}; }} "
            "QPushButton:disabled { "
            f"background: {colors['secondary_disabled_bg']}; "
            f"border-color: {colors['secondary_disabled_border']}; "
            f"color: {colors['secondary_disabled_text']}; }}"
        )

    def _apply_action_styles(self) -> None:
        if self._has_cert:
            primary_button = self._btn_export
        elif self._has_key:
            primary_button = self._btn_import
        else:
            primary_button = self._btn_generate
        for button in (self._btn_generate, self._btn_import, self._btn_export):
            button.setStyleSheet(self._button_style(button is primary_button))

    def update_slot(self, slot_info: SlotInfo) -> None:
        """Refresh card display from a SlotInfo."""
        colors = _get_card_colors()
        has_key = slot_info.has_key
        has_cert = slot_info.certificate is not None
        self._has_key = has_key
        self._has_cert = has_cert
        key_type = slot_info.key_type_str

        self._btn_generate.setText("Regenerate Key" if has_key else "Generate Key")
        self._btn_import.setText("Replace Cert" if has_cert else "Import Cert")
        self._btn_import.setEnabled(has_key)
        self._btn_import.setToolTip(
            "Import or replace the X.509 certificate for this slot"
            if has_key
            else "Generate or recover the private key first, then import the matching certificate"
        )
        self._btn_export.setVisible(has_cert)
        self._btn_delete.setVisible(has_cert)
        self._apply_action_styles()

        if not has_key and not has_cert:
            self._status_label.setText("- empty")
            self._status_label.setStyleSheet(f"color: {colors['secondary_text']}; font-size: 10pt;")
            self._status_label.setToolTip("Generate a new device-resident key in this slot.")
            self._hint_label.setText("Generate a key here, or use Verify PIN to Probe Keys if you expect one already exists.")
            self._hint_label.setStyleSheet(f"color: {colors['secondary_text']}; font-size: 9pt;")
        elif has_key and not has_cert:
            kt = key_type or "Key"
            self._status_label.setText(f"- {kt} present, cert missing")
            self._status_label.setStyleSheet("color: #b26a00; font-size: 10pt;")
            self._status_label.setToolTip(
                "Private key present. Next step: import the issued certificate for this slot."
            )
            self._hint_label.setText("Next step: import the issued certificate for this key.")
            self._hint_label.setStyleSheet("color: #b26a00; font-size: 9pt;")
        else:
            cert = slot_info.certificate
            subject_short = self._short_subject(cert.subject) if cert else ""
            expiry = self._format_expiry(cert.not_after) if cert else ""
            kt = key_type or ""
            if kt and subject_short:
                text = f"- {kt} - {subject_short}"
            elif subject_short:
                text = f"- {subject_short}"
            else:
                text = f"- {kt}" if kt else "- cert present"
            if expiry:
                text += f" - exp {expiry}"
            self._status_label.setText(text)
            self._status_label.setStyleSheet(f"color: {colors['text']}; font-size: 10pt;")
            self._status_label.setToolTip(
                f"{cert.subject}\nExpires: {expiry}" if cert and expiry else cert.subject if cert else "Ready for PIV use."
            )
            self._hint_label.setText("Ready for PIV use.")
            self._hint_label.setStyleSheet(f"color: {colors['secondary_text']}; font-size: 9pt;")

    def set_controls_enabled(self, enabled: bool) -> None:
        self._btn_generate.setEnabled(enabled)
        self._btn_import.setEnabled(enabled and self._has_key)
        self._btn_export.setEnabled(enabled and self._has_cert)
        self._btn_delete.setEnabled(enabled and self._has_cert)
        self._apply_action_styles()

    def _short_subject(self, subject: str) -> str:
        m = re.search(r'CN=([^,]+)', subject, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return subject.split(',')[0] if subject else ""

    def _format_expiry(self, not_after: str) -> str:
        if not not_after:
            return ""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return not_after[:10] if len(not_after) >= 10 else not_after


class PivTab(QWidget):
    """PIV tab for managing PIV keys and certificates."""

    piv_availability = Signal(bool)  # emitted once per device connect

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[PivWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._slot_cards: Dict[PivSlot, SlotCard] = {}
        self._slot_infos: Dict[PivSlot, SlotInfo] = {
            slot: SlotInfo(slot, False, None, None) for slot in PivSlot
        }
        self._session_key_cache_by_device: Dict[str, Dict[PivSlot, dict]] = {}
        self._last_generated_key_type: Optional[PivKeyType] = None
        self._controls_available = False
        self._reset_ready = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        warning_colors = _get_warning_colors()
        warning_style = (
            f"background-color: {warning_colors['bg']}; "
            f"border: 1px solid {warning_colors['border']}; "
            f"color: {warning_colors['text']}; "
            "padding: 10px; border-radius: 5px;"
        )

        # PCSC not running warning (shown when PCSC service is unavailable)
        self._pcsc_warning_label = QLabel(_get_pcsc_help_text())
        self._pcsc_warning_label.setStyleSheet(warning_style)
        self._pcsc_warning_label.setVisible(False)
        layout.addWidget(self._pcsc_warning_label)

        # PCSC library not available warning
        if not PCSC_AVAILABLE:
            lib_warning_label = QLabel(
                _get_pcsc_help_text() + "\n  • pip install pyscard"
            )
            lib_warning_label.setStyleSheet(warning_style)
            layout.addWidget(lib_warning_label)

        # --- Slot cards scroll area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(4)
        vbox.setContentsMargins(0, 0, 4, 0)

        for slot in PivSlot:
            card = SlotCard(slot, self)
            card.generate_requested.connect(self._generate_key_for)
            card.import_cert_requested.connect(self._import_certificate_for)
            card.export_cert_requested.connect(self._export_certificate_for)
            card.delete_requested.connect(self._delete_slot)
            self._slot_cards[slot] = card
            vbox.addWidget(card)

        vbox.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # --- PIN Management group ---
        pin_group = QGroupBox("PIN Management")
        pin_main = QVBoxLayout(pin_group)

        # PIN row
        pin_row = QHBoxLayout()
        pin_row.addWidget(QLabel("PIN:"))
        self._pin_status_label = QLabel("Unknown")
        pin_row.addWidget(self._pin_status_label)

        self._change_pin_button = QPushButton("Change PIN")
        self._change_pin_button.clicked.connect(self._change_pin)
        pin_row.addWidget(self._change_pin_button)

        self._unblock_pin_button = QPushButton("Unblock PIN")
        self._unblock_pin_button.clicked.connect(self._unblock_pin)
        pin_row.addWidget(self._unblock_pin_button)
        pin_row.addStretch()
        pin_main.addLayout(pin_row)

        # PUK row
        puk_row = QHBoxLayout()
        puk_row.addWidget(QLabel("PUK:"))
        self._puk_status_label = QLabel("Unknown")
        puk_row.addWidget(self._puk_status_label)

        self._change_puk_button = QPushButton("Change PUK")
        self._change_puk_button.clicked.connect(self._change_puk)
        puk_row.addWidget(self._change_puk_button)
        puk_row.addStretch()
        pin_main.addLayout(puk_row)

        hint = QLabel("Default PIN: 123456 | Default PUK: 12345678")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        pin_main.addWidget(hint)

        reset_hint = QLabel(
            "If both the PIN and PUK retry counters reach 0, PIV reset becomes available."
        )
        reset_hint.setWordWrap(True)
        reset_hint.setStyleSheet("color: gray; font-size: 10px;")
        pin_main.addWidget(reset_hint)

        probe_hint = QLabel(
            "If a slot still looks empty after reconnect, you can verify the PIN once to actively probe hidden keys."
        )
        probe_hint.setWordWrap(True)
        probe_hint.setStyleSheet("color: gray; font-size: 10px;")
        pin_main.addWidget(probe_hint)

        self._probe_keys_button = QPushButton("Verify PIN to Probe Keys")
        self._probe_keys_button.setToolTip(
            "Use your PIN once to probe slots that do not expose reliable metadata after reconnect."
        )
        self._probe_keys_button.clicked.connect(self._probe_keys_with_pin)
        pin_main.addWidget(self._probe_keys_button)

        # Danger zone sub-group
        danger_colors = _get_danger_zone_colors()
        danger_group = QGroupBox("Danger Zone")
        danger_group.setStyleSheet(f"""
            QGroupBox {{
                margin-top: 10px;
                padding: 14px 10px 10px 10px;
                border: 1px solid {danger_colors['border']};
                border-radius: 8px;
                background: {danger_colors['bg']};
                color: {danger_colors['text']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 2px 8px;
                border-radius: 4px;
                background: {danger_colors['title_bg']};
                color: {danger_colors['title_text']};
                font-weight: 600;
            }}
        """)
        danger_layout = QVBoxLayout(danger_group)
        danger_layout.setContentsMargins(10, 12, 10, 10)
        danger_info = QLabel("Resetting PIV requires both PIN and PUK to be blocked (0 retries).")
        danger_info.setWordWrap(True)
        danger_info.setStyleSheet(f"color: {danger_colors['text']};")
        danger_layout.addWidget(danger_info)
        self._reset_piv_button = QPushButton("Reset PIV Applet")
        self._reset_piv_button.setStyleSheet(f"""
            QPushButton {{
                color: {danger_colors['title_text']};
                border: 1px solid {danger_colors['border']};
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 600;
                background: transparent;
            }}
            QPushButton:hover {{
                background: {danger_colors['button_hover']};
            }}
            QPushButton:disabled {{
                border-color: {danger_colors['button_disabled_border']};
                color: {danger_colors['button_disabled_text']};
            }}
        """)
        self._reset_piv_button.clicked.connect(self._reset_piv)
        danger_layout.addWidget(self._reset_piv_button)
        self._danger_group = danger_group

        pin_main.addWidget(danger_group)
        layout.addWidget(pin_group)

        # --- Status bar ---
        status_layout = QHBoxLayout()

        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 0)
        self._status_progress.setVisible(False)
        self._status_progress.setMaximumWidth(120)

        self._status_label = QLabel("Ready")
        status_layout.addWidget(self._status_progress)
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()

        self._diagnose_button = QPushButton("Diagnose PCSC")
        self._diagnose_button.setToolTip(
            "Probe PCSC readers to see which applets (PIV, Provision, …) respond"
        )
        self._diagnose_button.clicked.connect(self._run_diagnose)
        status_layout.addWidget(self._diagnose_button)

        layout.addLayout(status_layout)

        # Initially disable controls
        self._set_controls_enabled(False)

    def set_device(self, device: SoloDevice) -> None:
        """Set the current device and check PIV availability."""
        self._device = device
        if getattr(device.mode, "value", None) != "regular":
            self.piv_availability.emit(False)
            self._cleanup_worker()
            self._set_controls_enabled(False)
            self._status_label.setText("PIV unavailable in bootloader mode")
            self._pcsc_warning_label.setVisible(False)
            return
        self.piv_availability.emit(self._should_show_tab())
        self._setup_worker()
        if self._worker:
            self._worker.probe_piv()

    def clear_device(self) -> None:
        """Clear the current device and reset all slot cards."""
        self._store_worker_key_cache()
        self._device = None
        self._cleanup_worker()
        self._slot_infos = {slot: SlotInfo(slot, False, None, None) for slot in PivSlot}
        for slot, card in self._slot_cards.items():
            card.update_slot(SlotInfo(slot, False, None, None))
        self._pin_status_label.setText("Unknown")
        self._puk_status_label.setText("Unknown")
        self._status_label.setText("No device connected")
        self._reset_ready = False
        self._set_controls_enabled(False)
        self._pcsc_warning_label.setVisible(False)

    def _setup_worker(self) -> None:
        if not self._device:
            return
        self._cleanup_worker()

        self._worker_thread = QThread()
        self._worker = PivWorker(self._device)
        self._worker.moveToThread(self._worker_thread)

        self._worker.piv_probed.connect(self._on_piv_probed)
        self._worker.slots_loaded.connect(self._on_slots_loaded)
        self._worker.key_generated.connect(self._on_key_generated)
        self._worker.key_deleted.connect(self._on_key_deleted)
        self._worker.certificate_imported.connect(self._on_certificate_imported)
        self._worker.certificate_exported.connect(self._on_certificate_exported)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.pin_status_updated.connect(self._on_pin_status_updated)
        self._worker.key_probe_completed.connect(self._on_key_probe_completed)
        self._worker.reset_completed.connect(self._on_reset_completed)
        self._worker.pcsc_status.connect(self._on_pcsc_status)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker.diagnose_result.connect(self._on_diagnose_result)

        self._worker.set_key_cache(self._get_session_key_cache())

        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

    def _device_cache_keys(self) -> list[str]:
        """Return candidate cache keys for the current device, ordered by stability."""
        if not self._device:
            return []

        keys = []

        device_uuid = getattr(self._device, "device_uuid", None)
        descriptor = getattr(self._device, "descriptor", None)
        descriptor_uuid = getattr(descriptor, "uuid", None)
        stable_uuid = device_uuid or descriptor_uuid
        if stable_uuid:
            keys.append(f"uuid:{stable_uuid}")

        path = getattr(self._device, "path", "") or ""
        if path:
            keys.append(path)

        descriptor_id = getattr(descriptor, "id", "") or ""
        if descriptor_id:
            keys.append(descriptor_id)

        deduped = []
        seen = set()
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _get_session_key_cache(self) -> Dict[PivSlot, dict]:
        """Return cached key hints for the current device, if any."""
        for cache_key in self._device_cache_keys():
            cache = self._session_key_cache_by_device.get(cache_key)
            if cache:
                return {slot: dict(info) for slot, info in cache.items()}
        return {}

    def _set_session_key_cache(self, cache: Dict[PivSlot, dict]) -> None:
        """Store the current device cache under all known aliases."""
        cache_keys = self._device_cache_keys()
        if not cache_keys:
            return

        normalized = {slot: dict(info) for slot, info in (cache or {}).items()}
        if normalized:
            for cache_key in cache_keys:
                self._session_key_cache_by_device[cache_key] = normalized
            return

        for cache_key in cache_keys:
            self._session_key_cache_by_device.pop(cache_key, None)

    def _store_worker_key_cache(self) -> None:
        """Persist the worker's session-local key hints across reconnects."""
        if not self._worker:
            return
        self._set_session_key_cache(self._worker.get_key_cache())

    def _set_controls_enabled(self, enabled: bool) -> None:
        pcsc_enabled = enabled and PCSC_AVAILABLE
        self._controls_available = pcsc_enabled
        for card in self._slot_cards.values():
            card.set_controls_enabled(pcsc_enabled)
        self._change_pin_button.setEnabled(pcsc_enabled)
        self._unblock_pin_button.setEnabled(pcsc_enabled)
        self._change_puk_button.setEnabled(pcsc_enabled)
        self._probe_keys_button.setEnabled(pcsc_enabled)
        self._diagnose_button.setEnabled(pcsc_enabled)
        self._update_reset_visibility()

    def _update_reset_visibility(
        self,
        pin_retries: Optional[int] = None,
        puk_retries: Optional[int] = None,
    ) -> None:
        if pin_retries is None and puk_retries is None:
            self._reset_ready = False
        else:
            self._reset_ready = pin_retries == 0 and puk_retries == 0
        visible = self._controls_available and self._reset_ready
        self._danger_group.setVisible(visible)
        self._reset_piv_button.setEnabled(visible)

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

    def _reload_slots(self) -> None:
        if not self._worker:
            return
        self._set_busy(True, "Loading slots...")
        self._worker.load_slots()

    def _update_pin_status(self) -> None:
        if not self._worker:
            return
        self._worker.get_pin_status()

    # ---- Signal handlers ----

    def _on_piv_probed(self, available: bool) -> None:
        self.piv_availability.emit(self._should_show_tab())
        if available:
            self._pcsc_warning_label.setVisible(False)
            self._set_controls_enabled(True)
            self._reload_slots()
            self._update_pin_status()
        else:
            self._set_controls_enabled(False)
            self._status_label.setText("PIV applet not available")

    def _on_slots_loaded(self, slot_infos: list) -> None:
        self._set_busy(False)
        for info in slot_infos:
            self._slot_infos[info.slot] = info
            card = self._slot_cards.get(info.slot)
            if card:
                card.update_slot(info)

    def _on_key_generated(self, success: bool, error: str, pubkey_der: bytes, slot) -> None:
        self._set_busy(False)
        if success:
            if slot is not None:
                card = self._slot_cards.get(slot)
                if card:
                    key_type_str = _KEY_TYPE_LABELS.get(self._last_generated_key_type)
                    slot_info = SlotInfo(slot, True, key_type_str, None)
                    self._slot_infos[slot] = slot_info
                    card.update_slot(slot_info)
                if self._last_generated_key_type is not None:
                    cache = self._get_session_key_cache()
                    cache[slot] = {
                        "key_type": self._last_generated_key_type,
                        "algorithm": _KEY_TYPE_LABELS.get(self._last_generated_key_type),
                        "has_certificate": False,
                    }
                    self._set_session_key_cache(cache)
            if pubkey_der:
                self._show_pubkey_dialog(pubkey_der)
            else:
                QMessageBox.information(
                    self,
                    "Success",
                    "Key generated successfully.\n\nNext step: import the matching certificate for this slot.",
                )
        else:
            QMessageBox.critical(self, "Error", f"Failed to generate key: {error}")

    def _on_key_deleted(self, success: bool, error: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", "Certificate deleted successfully")
            self._reload_slots()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete: {error}")

    def _on_certificate_imported(self, success: bool, error: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", "Certificate imported successfully.\n\nThis slot is ready for PIV use.")
            self._reload_slots()
        else:
            QMessageBox.critical(self, "Error", f"Failed to import certificate: {error}")

    def _on_certificate_exported(
        self, success: bool, error_or_path: str, cert_data: bytes
    ) -> None:
        self._set_busy(False)
        if success and cert_data:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Certificate",
                "certificate.der",
                "DER Certificate (*.der);;PEM Certificate (*.pem);;All Files (*)",
            )
            if file_path:
                try:
                    if file_path.endswith(".pem"):
                        try:
                            from cryptography import x509
                            from cryptography.hazmat.backends import default_backend
                            from cryptography.hazmat.primitives.serialization import Encoding
                            cert = x509.load_der_x509_certificate(cert_data, default_backend())
                            cert_data = cert.public_bytes(Encoding.PEM)
                        except ImportError:
                            QMessageBox.warning(
                                self,
                                "PEM Not Supported",
                                "PEM format requires cryptography library. Saving as DER.",
                            )
                            file_path = file_path.replace(".pem", ".der")
                    with open(file_path, "wb") as f:
                        f.write(cert_data)
                    QMessageBox.information(
                        self, "Success", f"Certificate saved to {file_path}"
                    )
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to save certificate: {e}")
        else:
            QMessageBox.critical(
                self, "Error", f"Failed to export certificate: {error_or_path}"
            )

    def _on_pin_status_updated(self, status: dict) -> None:
        if not status.get("pcsc_available", True):
            self._pin_status_label.setText("PCSC not available")
            self._puk_status_label.setText("PCSC not available")
            self._reset_ready = False
            self._update_reset_visibility()
            return
        if not status.get("connected", True):
            self._pin_status_label.setText("Not connected")
            self._puk_status_label.setText("Not connected")
            self._reset_ready = False
            self._update_reset_visibility()
            return

        pin_retries = status.get("pin_retries")
        puk_retries = status.get("puk_retries")

        self._pin_status_label.setText(
            f"{pin_retries} retries remaining" if pin_retries is not None else "Status unknown"
        )
        self._puk_status_label.setText(
            f"{puk_retries} retries remaining" if puk_retries is not None else "Status unknown"
        )
        self._update_reset_visibility(pin_retries, puk_retries)

    def _on_pin_changed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(
                self, "Success", message if message else "Operation completed successfully"
            )
            self._update_pin_status()
        else:
            QMessageBox.critical(self, "Error", message)

    def _on_key_probe_completed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            self._status_label.setText(message or "PIN verified")
            if message:
                QMessageBox.information(self, "PIN Verified", message)
        else:
            QMessageBox.critical(self, "PIN Verification Failed", message or "PIN verification failed")

    def _on_reset_completed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            self._set_session_key_cache({})
            QMessageBox.information(self, "Reset Complete", message)
            self._reload_slots()
            self._update_pin_status()
        else:
            error_msg = message
            if "6985" in message:
                error_msg += (
                    "\n\nTo reset PIV, you must first exhaust all PIN and PUK attempts:\n"
                    "1. Enter wrong PIN 3 times to block PIN\n"
                    "2. Enter wrong PUK 3 times to block PUK\n"
                    "3. Then you can reset PIV\n\n"
                    "Warning: This will erase all PIV keys and certificates!"
                )
            QMessageBox.critical(self, "Reset Failed", error_msg)

    def _on_pcsc_status(self, available: bool, message: str) -> None:
        if not available:
            self._status_label.setText(message)

    def _on_diagnose_result(self, report: str) -> None:
        self._set_busy(False)
        dlg = QDialog(self)
        dlg.setWindowTitle("PCSC Diagnostic")
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            "Which applets respond on the CCID interface:\n"
            "(SW=9000 = found, SW=6A82 = not registered)"
        ))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFontFamily("monospace")
        text.setPlainText(report)
        layout.addWidget(text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()

    def _on_error_occurred(self, error: str) -> None:
        self._set_busy(False)
        self._status_label.setText(f"Error: {error}")

    # ---- Slot card action handlers ----

    def _generate_key_for(self, slot: PivSlot) -> None:
        if not self._worker:
            return
        name, hex_id = _SLOT_META.get(slot, (str(slot), "??"))
        dialog = GenerateKeyDialog(self, slot)
        if dialog.exec() == QDialog.Accepted:
            self._last_generated_key_type = dialog.get_key_type()
            self._set_busy(True, f"Generating key in {name} ({hex_id})...")
            self._worker.generate_key(
                slot, dialog.get_key_type(),
                dialog.get_pin() or None,
                dialog.get_mgmt_key(),
            )

    def _import_certificate_for(self, slot: PivSlot) -> None:
        if not self._worker:
            return
        slot_info = self._slot_infos.get(slot)
        if slot_info is None or not slot_info.has_key:
            QMessageBox.information(
                self,
                "Generate Key First",
                "This slot does not currently expose a private key.\n\n"
                "Generate or recover the key first, then import the matching certificate.",
            )
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Certificate",
            "",
            "Certificates (*.pem *.crt *.cer *.der);;All Files (*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "rb") as f:
                cert_data = f.read()

            if b"-----BEGIN CERTIFICATE-----" in cert_data:
                try:
                    from cryptography import x509
                    from cryptography.hazmat.backends import default_backend
                    from cryptography.hazmat.primitives.serialization import Encoding
                    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
                    cert_data = cert.public_bytes(Encoding.DER)
                except ImportError:
                    QMessageBox.warning(
                        self,
                        "PEM Not Supported",
                        "PEM format requires cryptography library. Please use DER format.",
                    )
                    return

            # Ask for credentials
            name, hex_id = _SLOT_META.get(slot, (str(slot), "??"))
            cred_dialog = QDialog(self)
            cred_dialog.setWindowTitle(f"Import Certificate — {name} ({hex_id})")
            cred_layout = QVBoxLayout(cred_dialog)
            info_label = QLabel(
                "Import the issued X.509 certificate that matches the private key already stored in this slot."
            )
            info_label.setWordWrap(True)
            cred_layout.addWidget(info_label)
            form = QFormLayout()

            pin_input = QLineEdit()
            pin_input.setEchoMode(QLineEdit.Password)
            pin_input.setPlaceholderText("optional")
            form.addRow("PIN:", pin_input)

            mgmt_input = QLineEdit()
            mgmt_input.setText(DEFAULT_MANAGEMENT_KEY)
            form.addRow("Management Key:", mgmt_input)

            cred_layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(cred_dialog.accept)
            buttons.rejected.connect(cred_dialog.reject)
            cred_layout.addWidget(buttons)

            if cred_dialog.exec() == QDialog.Accepted:
                pin = pin_input.text()
                self._set_busy(True, "Importing certificate...")
                self._worker.import_certificate(
                    slot, cert_data, pin if pin else None, mgmt_input.text()
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read certificate: {e}")

    def _export_certificate_for(self, slot: PivSlot) -> None:
        if not self._worker:
            return
        self._set_busy(True, "Exporting certificate...")
        self._worker.export_certificate(slot)

    def _delete_slot(self, slot: PivSlot) -> None:
        if not self._worker:
            return
        name, hex_id = _SLOT_META.get(slot, (str(slot), "??"))
        reply = QMessageBox.question(
            self,
            "Confirm Certificate Delete",
            f"Delete the certificate in {name} ({hex_id})?\n\n"
            "The private key stays on the device.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        dialog = PivPinDialog(self, "Enter PIN", show_current=True, show_new=False)
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Deleting...")
            self._worker.delete_certificate(slot, dialog.get_current())

    # ---- PIN management handlers ----

    def _change_pin(self) -> None:
        if not self._worker:
            return
        dialog = PivPinDialog(
            self, "Change PIN",
            show_current=True, show_new=True,
            current_label="Current PIN:", new_label="New PIN:",
        )
        if dialog.exec() == QDialog.Accepted:
            current_pin = dialog.get_current()
            new_pin = dialog.get_new()
            if current_pin and new_pin:
                self._set_busy(True, "Changing PIN...")
                self._worker.change_pin(current_pin, new_pin)

    def _unblock_pin(self) -> None:
        if not self._worker:
            return
        dialog = PivPinDialog(
            self, "Unblock PIN",
            show_current=True, show_new=True,
            current_label="PUK:", new_label="New PIN:",
        )
        if dialog.exec() == QDialog.Accepted:
            puk = dialog.get_current()
            new_pin = dialog.get_new()
            if puk and new_pin:
                self._set_busy(True, "Unblocking PIN...")
                self._worker.unblock_pin(puk, new_pin)

    def _change_puk(self) -> None:
        if not self._worker:
            return
        dialog = PivPinDialog(
            self, "Change PUK",
            show_current=True, show_new=True,
            current_label="Current PUK:", new_label="New PUK:",
        )
        if dialog.exec() == QDialog.Accepted:
            current_puk = dialog.get_current()
            new_puk = dialog.get_new()
            if current_puk and new_puk:
                self._set_busy(True, "Changing PUK...")
                self._worker.change_puk(current_puk, new_puk)

    def _probe_keys_with_pin(self) -> None:
        if not self._worker:
            return
        dialog = PivPinDialog(
            self,
            "Verify PIN to Probe Keys",
            show_current=True,
            show_new=False,
            current_label="PIN:",
        )
        if dialog.exec() != QDialog.Accepted:
            return

        pin = dialog.get_current()
        if not pin:
            return

        self._set_busy(True, "Verifying PIN and probing slots...")
        self._worker.probe_slots_with_pin(pin)

    def _reset_piv(self) -> None:
        if not self._worker:
            return
        reply = QMessageBox.warning(
            self,
            "Reset PIV Applet",
            "This will permanently erase all PIV keys and certificates and reset "
            "the PIN/PUK to factory defaults.\n\n"
            "This requires both PIN and PUK to be blocked (0 retries remaining).\n\n"
            "Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            self._set_busy(True, "Resetting PIV applet...")
            self._worker.reset_piv()

    def _run_diagnose(self) -> None:
        if not self._worker:
            return
        self._set_busy(True, "Running PCSC diagnostic...")
        self._worker.diagnose_pcsc()

    def _show_pubkey_dialog(self, pubkey_der: bytes) -> None:
        try:
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PublicFormat, load_der_public_key
            )
            from cryptography.hazmat.backends import default_backend
            pub = load_der_public_key(pubkey_der, backend=default_backend())
            pem_text = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        except Exception:
            pem_text = pubkey_der.hex()

        dlg = QDialog(self)
        dlg.setWindowTitle("Public Key")
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)
        info = QLabel(
            "Key generated successfully.\n\n"
            "This slot now contains a private key, but it does not have a certificate yet. "
            "Use this public key to create or request the matching certificate, then return to this slot and choose Import Cert."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(pem_text)
        text.setFontFamily("monospace")
        layout.addWidget(text)
        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(pem_text))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        dlg.exec()
