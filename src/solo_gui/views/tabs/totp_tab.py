"""TOTP/Secrets tab for SoloKeys GUI.

Provides a comprehensive interface for managing TOTP credentials,
inspired by Nitrokey's secrets-app implementation.
"""

import os
from typing import Optional, List
import base64

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QMessageBox,
    QProgressBar,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QCheckBox,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QTextEdit,
    QApplication,
    QScrollArea,
    QFrame,
    QTabWidget,
)
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont

from solo_gui.models.device import SoloDevice, firmware_supports_extended_applets
from solo_gui.workers.totp_worker import (
    TotpWorker,
    Credential,
    OtpKind,
    OtherKind,
    Algorithm,
    OtpResult,
    SecretsAppStatus,
    FirmwareExtensionSpec,
    encode_password_only_label,
)
from .secrets_tools_tab import SecretsToolsTab


def _is_dark_mode() -> bool:
    """Detect if dark mode is enabled."""
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False
    color_scheme = QApplication.styleHints().colorScheme()
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


class CredentialCard(QFrame):
    """A card widget representing a single credential."""

    generate_requested = Signal(object)  # Credential
    delete_requested = Signal(object)    # Credential
    copy_requested = Signal(object)      # Credential
    load_password_requested = Signal(object)
    edit_password_requested = Signal(object)
    copy_login_requested = Signal(object)
    copy_password_requested = Signal(object)

    def __init__(self, credential: Credential, parent=None):
        super().__init__(parent)
        self._credential = credential
        self._code: str = ""

        colors = _get_card_colors()
        self.setObjectName("CredentialCard")
        self.setStyleSheet(f"""
            CredentialCard {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                background-color: {colors['bg']};
            }}
            CredentialCard:hover {{
                border-color: {colors['border']};
                background-color: {colors['hover']};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(6)

        # --- Left side ---
        left = QHBoxLayout()
        left.setSpacing(6)

        name_label = QLabel(credential.name)
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(11)
        name_label.setFont(name_font)
        left.addWidget(name_label)

        badges = []
        if credential.password_only:
            badges.append("Password")
        else:
            if credential.is_otp:
                badges.append(credential.kind_name)
            elif credential.other:
                badges.append(str(credential.other))
            if credential.has_password_safe:
                badges.append("Password")
        if not badges:
            badges.append("Secret")
        for badge_text in badges:
            type_badge = QLabel(badge_text)
            type_badge.setStyleSheet(
                "font-size: 10px; color: #555; padding: 1px 5px;"
                " border: 1px solid #ccc; border-radius: 3px;"
            )
            left.addWidget(type_badge)

        # Protection badge
        if credential.protected or credential.touch_required:
            parts = []
            if credential.protected:
                parts.append("PIN")
            if credential.touch_required:
                parts.append("Touch")
            prot_badge = QLabel("+".join(parts))
            prot_badge.setStyleSheet(
                "font-size: 10px; color: #2196F3; padding: 1px 5px;"
                " border: 1px solid #90caf9; border-radius: 3px;"
            )
            left.addWidget(prot_badge)

        left.addStretch()

        # --- Right side ---
        right = QHBoxLayout()
        right.setSpacing(4)

        # Code button (clickable to copy)
        self._code_btn = QPushButton("\u2500\u2500\u2500\u2500\u2500\u2500")
        code_font = QFont("monospace")
        code_font.setPointSize(13)
        code_font.setBold(True)
        self._code_btn.setFont(code_font)
        self._code_btn.setMinimumWidth(130)
        self._code_btn.setFlat(True)
        self._code_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; text-align: right; }"
        )
        self._code_btn.setCursor(Qt.ArrowCursor)
        self._code_btn.clicked.connect(self._copy_code)
        if credential.is_otp:
            right.addWidget(self._code_btn)

        # Copy symbol button
        copy_btn = QPushButton("\u2398")
        copy_btn.setFixedSize(28, 28)
        copy_btn.setFlat(True)
        copy_btn.setToolTip("Copy code")
        copy_btn.setStyleSheet(
            "QPushButton { color: #777; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #eee; }"
        )
        copy_btn.clicked.connect(self._copy_code)
        if credential.is_otp:
            right.addWidget(copy_btn)

        # Generate button
        self._gen_btn = QPushButton("\u25b6")
        self._gen_btn.setFixedSize(28, 28)
        self._gen_btn.setToolTip("Generate code")
        self._gen_btn.setStyleSheet(
            "QPushButton { border: 1px solid #ddd; border-radius: 4px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        self._gen_btn.clicked.connect(lambda: self.generate_requested.emit(self._credential))
        if credential.is_otp:
            right.addWidget(self._gen_btn)

        if credential.has_password_safe:
            button_style = (
                "QPushButton { color: #777; border: none; border-radius: 4px; }"
                "QPushButton:hover { background: #eee; }"
            )

            load_btn = QPushButton("\u21bb")
            load_btn.setFixedSize(28, 28)
            load_btn.setToolTip("Load password data")
            load_btn.setStyleSheet(button_style)
            load_btn.clicked.connect(lambda: self.load_password_requested.emit(self._credential))
            right.addWidget(load_btn)

            login_btn = QPushButton("\U0001f464")
            login_btn.setFixedSize(28, 28)
            login_btn.setToolTip("Copy login")
            login_btn.setStyleSheet(button_style)
            login_btn.clicked.connect(lambda: self.copy_login_requested.emit(self._credential))
            right.addWidget(login_btn)

            password_btn = QPushButton("\U0001f511")
            password_btn.setFixedSize(28, 28)
            password_btn.setToolTip("Copy password")
            password_btn.setStyleSheet(button_style)
            password_btn.clicked.connect(lambda: self.copy_password_requested.emit(self._credential))
            right.addWidget(password_btn)

            edit_btn = QPushButton("\u270e")
            edit_btn.setFixedSize(28, 28)
            edit_btn.setToolTip("Edit password data")
            edit_btn.setStyleSheet(button_style)
            edit_btn.clicked.connect(lambda: self.edit_password_requested.emit(self._credential))
            right.addWidget(edit_btn)

        # Delete button
        del_btn = QPushButton("\u2715")
        del_btn.setFixedSize(28, 28)
        del_btn.setToolTip("Delete credential")
        del_btn.setStyleSheet(
            "QPushButton { color: #cc0000; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #ffeaea; }"
        )
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._credential))
        right.addWidget(del_btn)

        layout.addLayout(left, stretch=1)
        layout.addLayout(right)

    @property
    def credential(self) -> Credential:
        return self._credential

    def set_code(self, code: str, countdown: int = 0) -> None:
        self._code = code
        display = self._format_code(code)
        if countdown > 0:
            display += f" ({countdown}s)"
        self._code_btn.setText(display)
        self._code_btn.setCursor(Qt.PointingHandCursor)
        self._code_btn.setToolTip("Click to copy")

    def clear_code(self) -> None:
        self._code = ""
        self._code_btn.setText("\u2500\u2500\u2500\u2500\u2500\u2500")
        self._code_btn.setCursor(Qt.ArrowCursor)
        self._code_btn.setToolTip("")

    def _format_code(self, code: str) -> str:
        """Format 6-digit as '123 456', 8-digit as '1234 5678'."""
        if len(code) == 6:
            return f"{code[:3]} {code[3:]}"
        if len(code) == 8:
            return f"{code[:4]} {code[4:]}"
        return code

    def _copy_code(self) -> None:
        if self._code:
            QApplication.clipboard().setText(self._code)
            self.copy_requested.emit(self._credential)


class SecretsPinDialog(QDialog):
    """Dialog for entering Secrets app PIN."""

    def __init__(self, parent=None, title="Enter PIN", is_new=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        if is_new:
            layout.addWidget(QLabel("Set a PIN for the Secrets app (min 4 characters):"))
            layout.addWidget(QLabel("You may need to touch the device button to confirm."))

        self._pin_input = QLineEdit()
        self._pin_input.setEchoMode(QLineEdit.Password)
        self._pin_input.setMinimumWidth(200)
        form.addRow("PIN:", self._pin_input)

        if is_new:
            self._confirm_input = QLineEdit()
            self._confirm_input.setEchoMode(QLineEdit.Password)
            form.addRow("Confirm:", self._confirm_input)
        else:
            self._confirm_input = None

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        pin = self._pin_input.text()
        if len(pin) < 4:
            QMessageBox.warning(self, "Invalid PIN", "PIN must be at least 4 characters.")
            return
        if self._confirm_input and pin != self._confirm_input.text():
            QMessageBox.warning(self, "PIN Mismatch", "PINs do not match.")
            return
        self.accept()

    def get_pin(self) -> str:
        return self._pin_input.text()


class AddCredentialDialog(QDialog):
    """Dialog for adding a new credential with optional OTP and password-safe data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Credential")
        self.setModal(True)
        self.setMinimumWidth(450)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., github.com:manuel")
        form.addRow("Name:", self._name_edit)

        name_hint = QLabel(
            "Best practice: include the site domain in the credential name, "
            "for example github.com:manuel, so the browser extension can match it automatically."
        )
        name_hint.setWordWrap(True)
        name_hint.setStyleSheet("color: #666; font-size: 12px;")
        form.addRow("", name_hint)

        self._otp_enabled = QCheckBox("Enable OTP")
        self._otp_enabled.setChecked(True)
        self._otp_enabled.toggled.connect(self._sync_sections)
        form.addRow(self._otp_enabled)

        self._password_enabled = QCheckBox("Enable Password Safe")
        self._password_enabled.setChecked(False)
        self._password_enabled.toggled.connect(self._sync_sections)
        form.addRow(self._password_enabled)

        layout.addLayout(form)

        self._otp_group = QGroupBox("OTP")
        otp_form = QFormLayout(self._otp_group)

        self._secret_edit = QLineEdit()
        self._secret_edit.setPlaceholderText("Base32 encoded secret")
        otp_form.addRow("Secret:", self._secret_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItem("TOTP (Time-based)", OtpKind.TOTP)
        self._type_combo.addItem("HOTP (Counter-based)", OtpKind.HOTP)
        otp_form.addRow("Type:", self._type_combo)

        self._algorithm_combo = QComboBox()
        self._algorithm_combo.addItem("SHA1 (Most compatible)", Algorithm.SHA1)
        self._algorithm_combo.addItem("SHA256", Algorithm.SHA256)
        self._algorithm_combo.addItem("SHA512", Algorithm.SHA512)
        otp_form.addRow("Algorithm:", self._algorithm_combo)

        self._digits_spin = QSpinBox()
        self._digits_spin.setRange(6, 8)
        self._digits_spin.setValue(6)
        otp_form.addRow("Digits:", self._digits_spin)

        self._period_spin = QSpinBox()
        self._period_spin.setRange(15, 120)
        self._period_spin.setValue(30)
        self._period_spin.setSuffix(" seconds")
        otp_form.addRow("Period:", self._period_spin)
        layout.addWidget(self._otp_group)

        self._password_group = QGroupBox("Password Safe")
        password_form = QFormLayout(self._password_group)
        self._login_edit = QLineEdit()
        self._login_edit.setPlaceholderText("Username or email")
        password_form.addRow("Login:", self._login_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.Password)
        self._password_edit.setPlaceholderText("Password")
        password_form.addRow("Password:", self._password_edit)

        self._notes_edit = QTextEdit()
        self._notes_edit.setMaximumHeight(120)
        self._notes_edit.setPlaceholderText("Notes")
        password_form.addRow("Notes:", self._notes_edit)
        layout.addWidget(self._password_group)

        security_group = QGroupBox("Security")
        security_form = QFormLayout(security_group)

        self._touch_checkbox = QCheckBox("Require touch to generate code")
        self._touch_checkbox.setChecked(True)
        security_form.addRow(self._touch_checkbox)

        self._protected_checkbox = QCheckBox("PIN-protected (encrypted storage)")
        self._protected_checkbox.setChecked(False)
        security_form.addRow(self._protected_checkbox)
        layout.addWidget(security_group)
        self._sync_sections()

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        name = self._name_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return

        if not self._otp_enabled.isChecked() and not self._password_enabled.isChecked():
            QMessageBox.warning(
                self,
                "Validation Error",
                "Enable OTP, Password Safe, or both.",
            )
            return

        if self._otp_enabled.isChecked():
            secret = self._secret_edit.text().strip().replace(" ", "").upper()
            if not secret:
                QMessageBox.warning(self, "Validation Error", "Secret is required when OTP is enabled.")
                return

            try:
                base64.b32decode(secret, casefold=True)
            except Exception:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "Invalid secret. Must be a valid Base32 string.",
                )
                return

        self.accept()

    def _sync_sections(self) -> None:
        otp_enabled = self._otp_enabled.isChecked()
        password_enabled = self._password_enabled.isChecked()
        self._otp_group.setVisible(otp_enabled)
        self._password_group.setVisible(password_enabled)
        self._touch_checkbox.setText(
            "Require touch before use"
            if password_enabled and not otp_enabled
            else "Require touch before use/generation"
        )

    def get_credential(self) -> Credential:
        """Get the credential from dialog inputs."""
        name = self._name_edit.text().strip()
        otp_enabled = self._otp_enabled.isChecked()

        return Credential(
            id=encode_password_only_label(name) if self._password_enabled.isChecked() and not otp_enabled else name.encode("utf-8"),
            otp=self._type_combo.currentData() if otp_enabled else None,
            algorithm=self._algorithm_combo.currentData(),
            digits=self._digits_spin.value(),
            period=self._period_spin.value(),
            login=self._login_edit.text().encode("utf-8") if self._password_enabled.isChecked() and self._login_edit.text() else None,
            password=self._password_edit.text().encode("utf-8") if self._password_enabled.isChecked() and self._password_edit.text() else None,
            metadata=self._notes_edit.toPlainText().encode("utf-8") if self._password_enabled.isChecked() and self._notes_edit.toPlainText() else None,
            touch_required=self._touch_checkbox.isChecked(),
            protected=self._protected_checkbox.isChecked(),
            has_password_safe=self._password_enabled.isChecked(),
        )

    def get_secret(self) -> bytes:
        """Get the decoded secret bytes."""
        if self._otp_enabled.isChecked():
            secret = self._secret_edit.text().strip().replace(" ", "").upper()
            return base64.b32decode(secret, casefold=True)
        return os.urandom(20)


class EditPasswordDialog(QDialog):
    """Edit password-safe fields for a credential."""

    def __init__(self, credential: Credential, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Credential: {credential.name}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(credential.name)
        form.addRow("Name:", self._name)

        self._login = QLineEdit((credential.login or b"").decode("utf-8", errors="replace"))
        form.addRow("Login:", self._login)

        self._password = QLineEdit((credential.password or b"").decode("utf-8", errors="replace"))
        self._password.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self._password)

        self._metadata = QTextEdit((credential.metadata or b"").decode("utf-8", errors="replace"))
        self._metadata.setMaximumHeight(120)
        form.addRow("Notes:", self._metadata)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str, str]:
        return (
            self._name.text().strip(),
            self._login.text(),
            self._password.text(),
            self._metadata.toPlainText(),
        )


class TotpTab(QWidget):
    """Unified Secrets tab for OTP and password-safe credentials."""

    totp_available = Signal(bool)  # emitted once per device connect after status probe

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[TotpWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._credentials: List[Credential] = []
        self._status: Optional[SecretsAppStatus] = None
        self._awaiting_touch_confirmation: bool = False
        self._last_generate_credential: Optional[Credential] = None
        self._pending_password_action: Optional[tuple[bytes, str]] = None
        # Per-credential countdown state: keyed by credential id (bytes)
        self._code_timers: dict = {}
        self._code_remaining: dict = {}
        self._credential_cards: dict = {}  # cred_id (bytes) → CredentialCard
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Status Group
        status_group = QGroupBox("Secrets App Status")
        status_layout = QVBoxLayout(status_group)

        status_info = QHBoxLayout()
        self._status_label = QLabel("Checking status...")
        status_info.addWidget(self._status_label)
        status_info.addStretch()

        self._pin_status_label = QLabel("")
        status_info.addWidget(self._pin_status_label)
        status_layout.addLayout(status_info)

        # Status actions
        status_actions = QHBoxLayout()
        self._check_status_button = QPushButton("Refresh")
        self._check_status_button.clicked.connect(self._check_status)
        status_actions.addWidget(self._check_status_button)

        self._set_pin_button = QPushButton("Set PIN")
        self._set_pin_button.clicked.connect(self._set_pin)
        self._set_pin_button.setEnabled(False)
        status_actions.addWidget(self._set_pin_button)

        self._verify_pin_button = QPushButton("Unlock")
        self._verify_pin_button.clicked.connect(self._verify_pin)
        self._verify_pin_button.setEnabled(False)
        status_actions.addWidget(self._verify_pin_button)

        self._change_pin_button = QPushButton("Change PIN")
        self._change_pin_button.clicked.connect(self._change_pin)
        self._change_pin_button.setEnabled(False)
        self._change_pin_button.setVisible(False)
        status_actions.addWidget(self._change_pin_button)

        status_actions.addStretch()
        status_layout.addLayout(status_actions)

        layout.addWidget(status_group)

        self._tabs = QTabWidget()

        secrets_page = QWidget()
        secrets_page_layout = QVBoxLayout(secrets_page)
        secrets_page_layout.setContentsMargins(0, 0, 0, 0)

        # Credentials Group
        creds_group = QGroupBox("Secrets")
        creds_layout = QVBoxLayout(creds_group)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Show:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("All")
        self._filter_combo.addItem("OTP")
        self._filter_combo.addItem("Password")
        self._filter_combo.addItem("OTP + Password")
        self._filter_combo.currentIndexChanged.connect(self._rebuild_cards)
        filters.addWidget(self._filter_combo)
        filters.addStretch()
        creds_layout.addLayout(filters)

        # Scrollable card list
        self._creds_scroll = QScrollArea()
        self._creds_scroll.setWidgetResizable(True)
        self._creds_scroll.setFrameShape(QFrame.NoFrame)

        self._creds_container = QWidget()
        self._creds_layout = QVBoxLayout(self._creds_container)
        self._creds_layout.setContentsMargins(2, 2, 2, 2)
        self._creds_layout.setSpacing(4)
        self._creds_layout.addStretch()

        self._creds_scroll.setWidget(self._creds_container)
        creds_layout.addWidget(self._creds_scroll)

        # Bottom bar: Add Credential
        actions = QHBoxLayout()
        self._add_button = QPushButton("Add Credential")
        self._add_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white; border: none;
                padding: 6px 14px; border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #aaa; }
        """)
        self._add_button.clicked.connect(self._add_credential)
        actions.addWidget(self._add_button)
        actions.addStretch()
        creds_layout.addLayout(actions)

        secrets_page_layout.addWidget(creds_group)

        self._tools_tab = SecretsToolsTab()

        self._tabs.addTab(secrets_page, "Credentials")
        self._tabs.addTab(self._tools_tab, "Tools")
        layout.addWidget(self._tabs)

        # Progress/Status bar
        progress_layout = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(False)
        self._progress_bar.setMaximumWidth(200)

        self._progress_label = QLabel("Ready")
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addStretch()

        layout.addLayout(progress_layout)

        # Disable controls initially
        self._set_controls_enabled(False)

    def set_device(self, device: SoloDevice) -> None:
        """Set the current device."""
        self._device = device
        self._setup_worker()
        self._tools_tab.set_device(device)
        self._set_controls_enabled(True)
        self.totp_available.emit(False)
        self._check_status()

    def clear_device(self) -> None:
        """Clear the current device."""
        self._device = None
        self._cleanup_worker()
        self._tools_tab.clear_device()
        self._credentials = []
        self._status = None
        self._credential_cards.clear()
        # Remove all card widgets from layout (keep the trailing stretch)
        while self._creds_layout.count() > 1:
            item = self._creds_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._status_label.setText("No device connected")
        self._pin_status_label.setText("")
        self._set_controls_enabled(False)

    def _setup_worker(self) -> None:
        """Setup the worker (no threading - operations are quick)."""
        if not self._device:
            return

        self._cleanup_worker()

        self._worker = TotpWorker(self._device)

        # Connect signals
        self._worker.status_checked.connect(self._on_status_checked)
        self._worker.credentials_loaded.connect(self._on_credentials_loaded)
        self._worker.credential_added.connect(self._on_credential_added)
        self._worker.credential_deleted.connect(self._on_credential_deleted)
        self._worker.credential_data_loaded.connect(self._on_credential_data_loaded)
        self._worker.credential_updated.connect(self._on_credential_updated)
        self._worker.otp_generated.connect(self._on_otp_generated)
        self._worker.pin_verified.connect(self._on_pin_verified)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.pin_required.connect(self._on_pin_required)
        self._worker.touch_required.connect(self._on_touch_required)
        self._worker.error_occurred.connect(self._on_error)

    def _cleanup_worker(self) -> None:
        """Cleanup worker and all per-credential timers."""
        for timer in self._code_timers.values():
            timer.stop()
        self._code_timers.clear()
        self._code_remaining.clear()

        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None

        self._worker = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable controls."""
        self._check_status_button.setEnabled(enabled)
        self._add_button.setEnabled(enabled)

    def _should_show_tab(self) -> bool:
        if self._device is None or getattr(self._device.mode, "value", None) != "regular":
            return False
        info = self._device.get_info()
        return firmware_supports_extended_applets(info.firmware_version)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Set busy state."""
        self._progress_bar.setVisible(busy)
        self._progress_label.setText(message if busy else "Ready")
        self._progress_label.setStyleSheet("")
        if busy:
            self._awaiting_touch_confirmation = False

    # =========================================================================
    # Actions
    # =========================================================================

    def _check_status(self) -> None:
        """Check secrets app status."""
        if not self._worker:
            return
        self._set_busy(True, "Checking status...")
        self._worker.check_status()

    def _refresh_credentials(self) -> None:
        """Refresh credentials list."""
        if not self._worker:
            return
        self._set_busy(True, "Loading credentials...")
        self._worker.load_credentials()

    def _add_credential(self) -> None:
        """Add a new credential."""
        if not self._worker:
            return

        dialog = AddCredentialDialog(self)
        if dialog.exec() == QDialog.Accepted:
            credential = dialog.get_credential()
            secret = dialog.get_secret()

            if credential.protected:
                if not self._worker.pin_is_set:
                    QMessageBox.warning(
                        self,
                        "PIN Required",
                        "Cannot create PIN-protected credential: No PIN is set on the device.\n\n"
                        "Please set a PIN first using the 'Set PIN' button."
                    )
                    return
                if not self._worker.pin_is_verified:
                    QMessageBox.warning(
                        self,
                        "Unlock Required",
                        "Cannot create PIN-protected credential: Device is locked.\n\n"
                        "Please unlock the device using the 'Unlock' button first."
                    )
                    return

            self._set_busy(True, "Adding credential...")
            self._worker.add_credential(credential, secret)

    def _generate_code_for(self, credential: Credential) -> None:
        """Generate OTP code for a specific credential."""
        if not self._worker or not credential.is_otp:
            return

        touch_confirmed = (
            self._awaiting_touch_confirmation
            and self._last_generate_credential is not None
            and self._last_generate_credential.id == credential.id
        )
        if touch_confirmed:
            self._awaiting_touch_confirmation = False
            self._last_generate_credential = None
        else:
            self._last_generate_credential = credential

        self._set_busy(True, "Generating code...")
        self._worker.generate_otp(credential, touch_confirmed=touch_confirmed)

    def _delete_credential_for(self, credential: Credential) -> None:
        """Delete a specific credential."""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete credential '{credential.name}'?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes and self._worker:
            self._set_busy(True, "Deleting credential...")
            self._worker.delete_credential(credential)

    def _load_password_for(self, credential: Credential, action: str = "load") -> None:
        if not self._worker or not credential.has_password_safe:
            return
        self._pending_password_action = (credential.id, action)
        self._set_busy(True, "Loading password data...")
        self._worker.load_credential_data(credential)

    def _copy_login_for(self, credential: Credential) -> None:
        loaded = self._find_credential(credential.id)
        if loaded and loaded.login is not None:
            QApplication.clipboard().setText(loaded.login.decode("utf-8", errors="replace"))
            self._progress_label.setText("Login copied to clipboard")
            return
        self._load_password_for(credential, "copy_login")

    def _copy_password_for(self, credential: Credential) -> None:
        loaded = self._find_credential(credential.id)
        if loaded and loaded.password is not None:
            QApplication.clipboard().setText(loaded.password.decode("utf-8", errors="replace"))
            self._progress_label.setText("Password copied to clipboard")
            return
        self._load_password_for(credential, "copy_password")

    def _edit_password_for(self, credential: Credential) -> None:
        loaded = self._find_credential(credential.id)
        if loaded and any(value is not None for value in (loaded.login, loaded.password, loaded.metadata)):
            self._open_edit_dialog(loaded)
            return
        self._load_password_for(credential, "edit")

    def _set_pin(self) -> None:
        """Set secrets app PIN."""
        if not self._worker:
            return

        dialog = SecretsPinDialog(self, "Set Secrets PIN", is_new=True)
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Setting PIN...")
            self._worker.set_new_pin(dialog.get_pin())

    def _verify_pin(self) -> None:
        """Verify/unlock secrets app."""
        if not self._worker:
            return

        dialog = SecretsPinDialog(self, "Enter Secrets PIN", is_new=False)
        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Verifying PIN...")
            self._worker.verify_pin(dialog.get_pin())

    def _change_pin(self) -> None:
        """Change the secrets app PIN."""
        if not self._worker:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Change Secrets PIN")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        current_pin = QLineEdit()
        current_pin.setEchoMode(QLineEdit.Password)
        form.addRow("Current PIN:", current_pin)

        new_pin = QLineEdit()
        new_pin.setEchoMode(QLineEdit.Password)
        form.addRow("New PIN:", new_pin)

        confirm_pin = QLineEdit()
        confirm_pin.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm New PIN:", confirm_pin)

        layout.addLayout(form)
        layout.addWidget(QLabel("You may need to touch the device button to confirm."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def validate():
            if len(new_pin.text()) < 4:
                QMessageBox.warning(dialog, "Invalid PIN", "PIN must be at least 4 characters.")
                return
            if new_pin.text() != confirm_pin.text():
                QMessageBox.warning(dialog, "PIN Mismatch", "New PINs do not match.")
                return
            dialog.accept()

        buttons.accepted.disconnect()
        buttons.accepted.connect(validate)

        if dialog.exec() == QDialog.Accepted:
            self._set_busy(True, "Changing PIN...")
            self._worker.change_pin(current_pin.text(), new_pin.text())

    # =========================================================================
    # Per-credential countdown helpers
    # =========================================================================

    def _update_code_display(self, cred_id: bytes, code: str, countdown: int = 0) -> None:
        """Update the code display on the card for the given credential id."""
        card = self._credential_cards.get(cred_id)
        if card:
            if code == "------":
                card.clear_code()
            else:
                card.set_code(code, countdown)

    def _start_countdown(self, cred_id: bytes, code: str, remaining: int) -> None:
        """Start (or restart) the per-credential countdown timer."""
        if cred_id in self._code_timers:
            self._code_timers[cred_id].stop()
            del self._code_timers[cred_id]
        self._code_remaining.pop(cred_id, None)

        if remaining <= 0:
            # HOTP or unknown period — show code without countdown
            self._update_code_display(cred_id, code)
            return

        self._code_remaining[cred_id] = remaining
        self._update_code_display(cred_id, code, remaining)

        timer = QTimer(self)
        self._code_timers[cred_id] = timer

        def on_tick():
            current = self._code_remaining.get(cred_id, 0) - 1
            self._code_remaining[cred_id] = current
            if current <= 0:
                timer.stop()
                self._code_timers.pop(cred_id, None)
                self._code_remaining.pop(cred_id, None)
                self._update_code_display(cred_id, "------")
            else:
                self._update_code_display(cred_id, code, current)

        timer.timeout.connect(on_tick)
        timer.start(1000)

    # =========================================================================
    # Signal Handlers
    # =========================================================================

    def _on_status_checked(self, status: SecretsAppStatus) -> None:
        """Handle status check result."""
        self._set_busy(False)
        self._status = status
        self.totp_available.emit(status.supported and self._should_show_tab())

        if status.supported:
            self._status_label.setText(
                f"Secrets App v{status.version} - {status.credentials_count}/{status.max_credentials} credentials"
            )
            self._status_label.setStyleSheet("color: green;")

            if status.pin_set:
                self._pin_status_label.setText("PIN set")
                self._set_pin_button.setEnabled(False)
                self._set_pin_button.setVisible(True)
                self._verify_pin_button.setEnabled(True)
                self._verify_pin_button.setVisible(True)
                self._change_pin_button.setEnabled(False)
                self._change_pin_button.setVisible(False)
                if status.pin_attempts_remaining:
                    self._pin_status_label.setText(
                        f"PIN set ({status.pin_attempts_remaining} attempts remaining)"
                    )
            else:
                self._pin_status_label.setText("No PIN set")
                self._set_pin_button.setEnabled(True)
                self._set_pin_button.setVisible(True)
                self._verify_pin_button.setEnabled(False)
                self._verify_pin_button.setVisible(False)
                self._change_pin_button.setEnabled(False)
                self._change_pin_button.setVisible(False)

            self._refresh_credentials()
        else:
            self._status_label.setText("Secrets App not available")
            self._status_label.setStyleSheet("color: gray;")
            self._pin_status_label.setText("")

    def _show_firmware_info(self) -> None:
        """Show firmware extension requirements."""
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Firmware Extension Required")
        msg.setText("TOTP/Secrets functionality requires a firmware extension.")
        msg.setDetailedText(FirmwareExtensionSpec.get_integration_plan())
        msg.exec()

    def _on_credentials_loaded(self, credentials: List[Credential]) -> None:
        """Handle credentials loaded."""
        self._set_busy(False)

        # Stop all existing countdown timers on refresh
        for timer in self._code_timers.values():
            timer.stop()
        self._code_timers.clear()
        self._code_remaining.clear()
        self._credential_cards.clear()

        self._credentials = credentials
        if self._status and self._status.supported:
            self._status.credentials_count = len(credentials)
            self._status_label.setText(
                f"Secrets App v{self._status.version} - {len(credentials)}/{self._status.max_credentials} credentials"
            )
        self._rebuild_cards()

    def _on_credential_added(self, success: bool, error: str) -> None:
        """Handle credential added."""
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", "Credential added successfully")
            self._refresh_credentials()
        else:
            QMessageBox.critical(self, "Error", f"Failed to add credential: {error}")

    def _on_credential_deleted(self, success: bool, error: str) -> None:
        """Handle credential deleted."""
        self._set_busy(False)
        if success:
            self._progress_label.setText("Credential deleted")
            self._refresh_credentials()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete: {error}")

    def _on_otp_generated(self, result: OtpResult) -> None:
        """Handle OTP generated — start per-credential countdown."""
        self._set_busy(False)
        self._start_countdown(result.credential.id, result.code, result.remaining_seconds)

    def _on_card_copy(self, credential: Credential) -> None:
        """Handle copy requested from a card."""
        self._progress_label.setText("Code copied to clipboard")

    def _on_credential_data_loaded(self, credential: Credential) -> None:
        self._set_busy(False)
        self._merge_credential(credential)
        pending = self._pending_password_action
        self._pending_password_action = None
        if not pending or pending[0] != credential.id:
            self._progress_label.setText("Password data loaded")
            return

        action = pending[1]
        if action == "copy_login" and credential.login is not None:
            QApplication.clipboard().setText(credential.login.decode("utf-8", errors="replace"))
            self._progress_label.setText("Login copied to clipboard")
        elif action == "copy_password" and credential.password is not None:
            QApplication.clipboard().setText(credential.password.decode("utf-8", errors="replace"))
            self._progress_label.setText("Password copied to clipboard")
        elif action == "edit":
            self._open_edit_dialog(credential)
        else:
            self._progress_label.setText("Password data loaded")

    def _on_credential_updated(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Updated", "Credential updated.")
            self._refresh_credentials()
        else:
            QMessageBox.warning(self, "Update failed", message)

    def _on_pin_verified(self, success: bool, message: str) -> None:
        """Handle PIN verification result."""
        self._set_busy(False)
        if success:
            self._progress_label.setText("PIN verified - Secrets unlocked")
            self._verify_pin_button.setEnabled(False)
            self._verify_pin_button.setVisible(False)
            self._change_pin_button.setEnabled(True)
            self._change_pin_button.setVisible(True)
            self._refresh_credentials()
        else:
            QMessageBox.warning(self, "PIN Error", message or "PIN verification failed")

    def _on_pin_changed(self, success: bool, message: str) -> None:
        """Handle PIN change result."""
        self._set_busy(False)
        if success:
            QMessageBox.information(
                self, "Success", message or "PIN set successfully"
            )
            self._check_status()
        else:
            QMessageBox.critical(self, "Error", message)

    def _on_pin_required(self) -> None:
        """Handle PIN required signal."""
        self._set_busy(False)
        self._verify_pin()

    def _on_touch_required(self) -> None:
        """Handle touch required signal - user must click Generate again to confirm."""
        self._set_busy(False)
        self._awaiting_touch_confirmation = True
        self._progress_label.setText("Touch required - click Generate again to confirm")
        self._progress_label.setStyleSheet("color: orange; font-weight: bold;")

    def _on_error(self, error: str) -> None:
        """Handle worker error."""
        self._set_busy(False)
        self._pending_password_action = None
        QMessageBox.critical(self, "Error", error)

    def _rebuild_cards(self) -> None:
        while self._creds_layout.count() > 1:
            item = self._creds_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._credential_cards.clear()

        for cred in self._filtered_credentials():
            card = CredentialCard(cred, self._creds_container)
            card.generate_requested.connect(self._generate_code_for)
            card.delete_requested.connect(self._delete_credential_for)
            card.copy_requested.connect(self._on_card_copy)
            card.load_password_requested.connect(lambda credential: self._load_password_for(credential, "load"))
            card.edit_password_requested.connect(self._edit_password_for)
            card.copy_login_requested.connect(self._copy_login_for)
            card.copy_password_requested.connect(self._copy_password_for)
            self._creds_layout.insertWidget(self._creds_layout.count() - 1, card)
            self._credential_cards[cred.id] = card

    def _filtered_credentials(self) -> List[Credential]:
        mode = self._filter_combo.currentText()
        if mode == "OTP":
            return [cred for cred in self._credentials if cred.is_otp]
        if mode == "Password":
            return [cred for cred in self._credentials if cred.has_password_safe]
        if mode == "OTP + Password":
            return [cred for cred in self._credentials if cred.is_otp and cred.has_password_safe]
        return list(self._credentials)

    def _find_credential(self, cred_id: bytes) -> Optional[Credential]:
        for credential in self._credentials:
            if credential.id == cred_id:
                return credential
        return None

    def _merge_credential(self, updated: Credential) -> None:
        for index, credential in enumerate(self._credentials):
            if credential.id == updated.id:
                self._credentials[index] = updated
                break
        else:
            self._credentials.append(updated)
        self._rebuild_cards()

    def _open_edit_dialog(self, credential: Credential) -> None:
        if not self._worker:
            return
        dialog = EditPasswordDialog(credential, self)
        if dialog.exec() != QDialog.Accepted:
            return
        name, login, password, metadata = dialog.values()
        self._set_busy(True, "Updating credential...")
        self._worker.update_credential_data(
            credential,
            new_name=(
                encode_password_only_label(name).decode("utf-8")
                if credential.password_only and name and name != credential.name
                else (name if name and name != credential.name else None)
            ),
            login=login,
            password=password,
            metadata=metadata,
        )
