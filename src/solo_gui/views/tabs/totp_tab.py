"""TOTP/Secrets tab for SoloKeys GUI.

Provides a comprehensive interface for managing TOTP credentials,
inspired by Nitrokey's secrets-app implementation.
"""

from typing import Optional, List
import base64

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QHeaderView,
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
    QTabWidget,
)
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont

from solo_gui.models.device import SoloDevice
from solo_gui.workers.totp_worker import (
    TotpWorker,
    Credential,
    OtpKind,
    OtherKind,
    Algorithm,
    OtpResult,
    SecretsAppStatus,
    FirmwareExtensionSpec,
)


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
    """Dialog for adding a new TOTP/Secrets credential."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Credential")
        self.setModal(True)
        self.setMinimumWidth(450)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Create tabs for different credential types
        tabs = QTabWidget()

        # TOTP tab
        totp_widget = QWidget()
        totp_layout = QFormLayout(totp_widget)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., GitHub, Google")
        totp_layout.addRow("Name:", self._name_edit)

        self._secret_edit = QLineEdit()
        self._secret_edit.setPlaceholderText("Base32 encoded secret")
        totp_layout.addRow("Secret:", self._secret_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItem("TOTP (Time-based)", OtpKind.TOTP)
        self._type_combo.addItem("HOTP (Counter-based)", OtpKind.HOTP)
        totp_layout.addRow("Type:", self._type_combo)

        self._algorithm_combo = QComboBox()
        self._algorithm_combo.addItem("SHA1 (Most compatible)", Algorithm.SHA1)
        self._algorithm_combo.addItem("SHA256", Algorithm.SHA256)
        self._algorithm_combo.addItem("SHA512", Algorithm.SHA512)
        totp_layout.addRow("Algorithm:", self._algorithm_combo)

        self._digits_spin = QSpinBox()
        self._digits_spin.setRange(6, 8)
        self._digits_spin.setValue(6)
        totp_layout.addRow("Digits:", self._digits_spin)

        self._period_spin = QSpinBox()
        self._period_spin.setRange(15, 120)
        self._period_spin.setValue(30)
        self._period_spin.setSuffix(" seconds")
        totp_layout.addRow("Period:", self._period_spin)

        tabs.addTab(totp_widget, "OTP Settings")

        # Password Safe tab
        pws_widget = QWidget()
        pws_layout = QFormLayout(pws_widget)

        self._login_edit = QLineEdit()
        self._login_edit.setPlaceholderText("Username (optional)")
        pws_layout.addRow("Login:", self._login_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setPlaceholderText("Password (optional)")
        self._password_edit.setEchoMode(QLineEdit.Password)
        pws_layout.addRow("Password:", self._password_edit)

        self._metadata_edit = QTextEdit()
        self._metadata_edit.setPlaceholderText("Notes or comments (optional)")
        self._metadata_edit.setMaximumHeight(80)
        pws_layout.addRow("Notes:", self._metadata_edit)

        tabs.addTab(pws_widget, "Password Safe")

        # Security tab
        sec_widget = QWidget()
        sec_layout = QFormLayout(sec_widget)

        self._touch_checkbox = QCheckBox("Require touch to generate code")
        self._touch_checkbox.setChecked(True)
        sec_layout.addRow(self._touch_checkbox)

        self._protected_checkbox = QCheckBox("PIN-protected (encrypted storage)")
        self._protected_checkbox.setChecked(False)
        sec_layout.addRow(self._protected_checkbox)

        tabs.addTab(sec_widget, "Security")

        layout.addWidget(tabs)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        name = self._name_edit.text().strip()
        secret = self._secret_edit.text().strip().replace(" ", "").upper()

        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return

        if not secret:
            QMessageBox.warning(self, "Validation Error", "Secret is required.")
            return

        # Validate base32 secret
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

    def get_credential(self) -> Credential:
        """Get the credential from dialog inputs."""
        name = self._name_edit.text().strip()
        otp_kind = self._type_combo.currentData()

        login = self._login_edit.text().strip()
        password = self._password_edit.text()
        metadata = self._metadata_edit.toPlainText().strip()

        return Credential(
            id=name.encode("utf-8"),
            otp=otp_kind,
            algorithm=self._algorithm_combo.currentData(),
            digits=self._digits_spin.value(),
            period=self._period_spin.value(),
            touch_required=self._touch_checkbox.isChecked(),
            protected=self._protected_checkbox.isChecked(),
            login=login.encode("utf-8") if login else None,
            password=password.encode("utf-8") if password else None,
            metadata=metadata.encode("utf-8") if metadata else None,
        )

    def get_secret(self) -> bytes:
        """Get the decoded secret bytes."""
        secret = self._secret_edit.text().strip().replace(" ", "").upper()
        return base64.b32decode(secret, casefold=True)


class TotpTab(QWidget):
    """TOTP/Secrets tab for managing 2FA credentials."""

    totp_available = Signal(bool)  # emitted once per device connect after status probe

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[TotpWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._update_timer: Optional[QTimer] = None
        self._credentials: List[Credential] = []
        self._status: Optional[SecretsAppStatus] = None
        self._awaiting_touch_confirmation: bool = False
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
        self._check_status_button = QPushButton("Check Status")
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
        self._change_pin_button.setVisible(False)  # Hidden by default
        status_actions.addWidget(self._change_pin_button)

        status_actions.addStretch()
        status_layout.addLayout(status_actions)

        layout.addWidget(status_group)

        # Credentials Group
        creds_group = QGroupBox("OTP Credentials")
        creds_layout = QVBoxLayout(creds_group)

        # Credentials table
        self._creds_table = QTableWidget()
        self._creds_table.setColumnCount(6)
        self._creds_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Algorithm", "Digits", "Protected", "Code"]
        )
        self._creds_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._creds_table.setSelectionBehavior(QTableWidget.SelectRows)

        header = self._creds_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        creds_layout.addWidget(self._creds_table)

        # Credential actions
        actions = QHBoxLayout()

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self._refresh_credentials)
        actions.addWidget(self._refresh_button)

        self._add_button = QPushButton("Add Credential")
        self._add_button.clicked.connect(self._add_credential)
        actions.addWidget(self._add_button)

        self._delete_button = QPushButton("Delete")
        self._delete_button.clicked.connect(self._delete_credential)
        self._delete_button.setEnabled(False)
        actions.addWidget(self._delete_button)

        self._generate_button = QPushButton("Generate Code")
        self._generate_button.clicked.connect(self._generate_code)
        self._generate_button.setEnabled(False)
        actions.addWidget(self._generate_button)

        self._copy_button = QPushButton("Copy Code")
        self._copy_button.clicked.connect(self._copy_code)
        self._copy_button.setEnabled(False)
        actions.addWidget(self._copy_button)

        actions.addStretch()
        creds_layout.addLayout(actions)

        layout.addWidget(creds_group)

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

        # Time remaining for current code
        self._time_label = QLabel("")
        self._time_label.setFont(QFont("monospace", 10))
        progress_layout.addWidget(self._time_label)

        layout.addLayout(progress_layout)

        # Connect selection change
        self._creds_table.itemSelectionChanged.connect(self._on_selection_changed)

        # Disable controls initially
        self._set_controls_enabled(False)

    def set_device(self, device: SoloDevice) -> None:
        """Set the current device."""
        self._device = device
        self._setup_worker()
        self._set_controls_enabled(True)
        self._check_status()

    def clear_device(self) -> None:
        """Clear the current device."""
        self._device = None
        self._cleanup_worker()
        self._credentials = []
        self._status = None
        self._creds_table.setRowCount(0)
        self._status_label.setText("No device connected")
        self._pin_status_label.setText("")
        self._set_controls_enabled(False)

    def _setup_worker(self) -> None:
        """Setup the worker (no threading - operations are quick)."""
        if not self._device:
            return

        self._cleanup_worker()

        # Create worker without threading - CTAPHID operations are quick
        self._worker = TotpWorker(self._device)

        # Connect signals
        self._worker.status_checked.connect(self._on_status_checked)
        self._worker.credentials_loaded.connect(self._on_credentials_loaded)
        self._worker.credential_added.connect(self._on_credential_added)
        self._worker.credential_deleted.connect(self._on_credential_deleted)
        self._worker.otp_generated.connect(self._on_otp_generated)
        self._worker.pin_verified.connect(self._on_pin_verified)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.pin_required.connect(self._on_pin_required)
        self._worker.touch_required.connect(self._on_touch_required)
        self._worker.error_occurred.connect(self._on_error)

    def _cleanup_worker(self) -> None:
        """Cleanup worker."""
        if self._update_timer:
            self._update_timer.stop()
            self._update_timer = None

        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None

        self._worker = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable controls."""
        self._check_status_button.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)
        self._add_button.setEnabled(enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Set busy state."""
        self._progress_bar.setVisible(busy)
        self._progress_label.setText(message if busy else "Ready")
        self._progress_label.setStyleSheet("")  # Reset any styling
        if busy:
            self._awaiting_touch_confirmation = False

    def _on_selection_changed(self) -> None:
        """Handle credential selection change."""
        has_selection = bool(self._creds_table.selectedItems())
        row = self._creds_table.currentRow()
        is_otp = False
        if row >= 0 and row < len(self._credentials):
            is_otp = self._credentials[row].is_otp

        self._delete_button.setEnabled(has_selection)
        self._generate_button.setEnabled(has_selection and is_otp)
        self._copy_button.setEnabled(has_selection)

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

            # Check if trying to create PIN-protected credential
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
                    # Prompt for PIN now so the worker can cache it and re-verify
                    # immediately before sending the PUT command.
                    self._verify_pin()
                    if not self._worker.pin_is_verified:
                        return

            self._set_busy(True, "Adding credential...")
            self._worker.add_credential(credential, secret)

    def _delete_credential(self) -> None:
        """Delete selected credential."""
        row = self._creds_table.currentRow()
        if row < 0 or row >= len(self._credentials):
            return

        credential = self._credentials[row]

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete credential '{credential.name}'?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes and self._worker:
            self._set_busy(True, "Deleting credential...")
            self._worker.delete_credential(credential)

    def _generate_code(self) -> None:
        """Generate OTP code for selected credential."""
        row = self._creds_table.currentRow()
        if row < 0 or row >= len(self._credentials):
            return

        credential = self._credentials[row]
        if not credential.is_otp:
            return

        if self._worker:
            # Check if this is a confirmation after touch was required
            touch_confirmed = self._awaiting_touch_confirmation
            if touch_confirmed:
                self._awaiting_touch_confirmation = False
                self._set_busy(True, "Generating code...")
            else:
                self._set_busy(True, "Generating code...")

            self._worker.generate_otp(credential, touch_confirmed=touch_confirmed)

    def _copy_code(self) -> None:
        """Copy current code to clipboard."""
        row = self._creds_table.currentRow()
        if row < 0:
            return

        code_item = self._creds_table.item(row, 5)
        if code_item and code_item.text() and code_item.text() != "------":
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(code_item.text())
            self._progress_label.setText("Code copied to clipboard")

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

        # Dialog for changing PIN
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
    # Signal Handlers
    # =========================================================================

    def _on_status_checked(self, status: SecretsAppStatus) -> None:
        """Handle status check result."""
        self._set_busy(False)
        self._status = status
        self.totp_available.emit(status.supported)

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
        self._credentials = credentials

        self._creds_table.setRowCount(0)
        for i, cred in enumerate(credentials):
            self._creds_table.insertRow(i)

            self._creds_table.setItem(i, 0, QTableWidgetItem(cred.name))

            # Type
            if cred.otp:
                type_str = str(cred.otp)
            elif cred.other:
                type_str = str(cred.other)
            else:
                type_str = "Password"
            self._creds_table.setItem(i, 1, QTableWidgetItem(type_str))

            # Algorithm
            self._creds_table.setItem(i, 2, QTableWidgetItem(cred.algorithm.name))

            # Digits
            self._creds_table.setItem(i, 3, QTableWidgetItem(str(cred.digits)))

            # Protected
            protected_str = "Yes" if cred.protected else "No"
            if cred.touch_required:
                protected_str += " + Touch"
            self._creds_table.setItem(i, 4, QTableWidgetItem(protected_str))

            # Code placeholder
            self._creds_table.setItem(i, 5, QTableWidgetItem("------"))

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
            QMessageBox.information(self, "Success", "Credential deleted")
            self._refresh_credentials()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete: {error}")

    def _on_otp_generated(self, result: OtpResult) -> None:
        """Handle OTP generated."""
        self._set_busy(False)

        row = self._creds_table.currentRow()
        if row >= 0:
            code_item = QTableWidgetItem(result.code)
            code_item.setFont(QFont("monospace", 12, QFont.Bold))
            self._creds_table.setItem(row, 5, code_item)

        # Update time display
        self._update_time_display(result.remaining_seconds)

        # Start timer to update remaining time
        if self._update_timer:
            self._update_timer.stop()

        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._tick_time)
        self._update_timer.start(1000)
        self._remaining_seconds = result.remaining_seconds

    def _update_time_display(self, seconds: int) -> None:
        """Update the time remaining display."""
        if seconds > 0:
            self._time_label.setText(f"Valid for {seconds}s")
        else:
            self._time_label.setText("Expired")

    def _tick_time(self) -> None:
        """Timer tick for countdown."""
        self._remaining_seconds -= 1
        if self._remaining_seconds <= 0:
            self._update_timer.stop()
            self._time_label.setText("Expired - Generate new code")
            # Clear code from table
            row = self._creds_table.currentRow()
            if row >= 0:
                self._creds_table.setItem(row, 5, QTableWidgetItem("------"))
        else:
            self._update_time_display(self._remaining_seconds)

    def _on_pin_verified(self, success: bool, message: str) -> None:
        """Handle PIN verification result."""
        self._set_busy(False)
        if success:
            self._progress_label.setText("PIN verified - Secrets unlocked")
            self._verify_pin_button.setEnabled(False)  # Disable after successful unlock
            self._verify_pin_button.setVisible(False)
            self._change_pin_button.setEnabled(True)  # Show change PIN after unlock
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
        QMessageBox.critical(self, "Error", error)
