"""FIDO2 tab for SoloKeys GUI."""

import os
import sys
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication,
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
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QFormLayout,
)
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication

from solo_gui.models.device import SoloDevice
from solo_gui.utils.windows_elevation import (
    can_restart_as_admin,
    is_windows_admin,
    restart_as_admin,
)
from solo_gui.workers.fido2_worker import Fido2Worker, Fido2Credential


def _is_dark_mode() -> bool:
    """Detect if dark mode is enabled."""
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False
    color_scheme = QGuiApplication.styleHints().colorScheme()
    return color_scheme == Qt.ColorScheme.Dark


def _get_warning_colors() -> dict:
    """Get theme-aware colors for warning banners."""
    if _is_dark_mode():
        return {
            "bg": "#4a3b12",
            "border": "#8a6d1f",
            "text": "#f3e3a1",
        }
    return {
        "bg": "#fff3cd",
        "border": "#e0c36d",
        "text": "#664d03",
    }


class PinDialog(QDialog):
    """Dialog for entering PIN."""

    def __init__(self, parent=None, title="Enter PIN", message="Enter your device PIN:"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Message
        layout.addWidget(QLabel(message))

        # PIN input
        form_layout = QFormLayout()
        self._pin_input = QLineEdit()
        self._pin_input.setEchoMode(QLineEdit.Password)
        self._pin_input.setMinimumWidth(200)
        form_layout.addRow("PIN:", self._pin_input)
        layout.addLayout(form_layout)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_pin(self) -> str:
        return self._pin_input.text()


class ChangePinDialog(QDialog):
    """Dialog for changing PIN."""

    def __init__(self, parent=None, is_new_pin=False):
        super().__init__(parent)
        self.setWindowTitle("Set New PIN" if is_new_pin else "Change PIN")
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Form
        form_layout = QFormLayout()

        if not is_new_pin:
            self._current_pin = QLineEdit()
            self._current_pin.setEchoMode(QLineEdit.Password)
            form_layout.addRow("Current PIN:", self._current_pin)
        else:
            self._current_pin = None

        self._new_pin = QLineEdit()
        self._new_pin.setEchoMode(QLineEdit.Password)
        form_layout.addRow("New PIN:", self._new_pin)

        self._confirm_pin = QLineEdit()
        self._confirm_pin.setEchoMode(QLineEdit.Password)
        form_layout.addRow("Confirm PIN:", self._confirm_pin)

        layout.addLayout(form_layout)

        # Info label
        layout.addWidget(QLabel("PIN must be at least 4 characters."))

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        new_pin = self._new_pin.text()
        confirm_pin = self._confirm_pin.text()

        if len(new_pin) < 4:
            QMessageBox.warning(self, "Invalid PIN", "PIN must be at least 4 characters.")
            return

        if new_pin != confirm_pin:
            QMessageBox.warning(self, "PIN Mismatch", "The PINs do not match.")
            return

        self.accept()

    def get_current_pin(self) -> Optional[str]:
        return self._current_pin.text() if self._current_pin else None

    def get_new_pin(self) -> str:
        return self._new_pin.text()


class Fido2Tab(QWidget):
    """FIDO2 tab for managing webauthn credentials."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[Fido2Worker] = None
        self._worker_thread: Optional[QThread] = None
        self._credentials: List[Fido2Credential] = []  # Store loaded credentials
        self._pin_set: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        layout = QVBoxLayout(self)

        # Credentials Group
        credentials_group = QGroupBox("FIDO2 Credentials")
        credentials_layout = QVBoxLayout(credentials_group)

        # Credentials table
        self._credentials_table = QTableWidget()
        self._credentials_table.setColumnCount(4)
        self._credentials_table.setHorizontalHeaderLabels(
            ["User", "RP ID", "RP Name", "Algorithm"]
        )

        # Make table read-only
        self._credentials_table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Stretch the last column
        header = self._credentials_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        credentials_layout.addWidget(self._credentials_table)

        # Explain the discoverable-only limitation
        note = QLabel(
            "Only discoverable (resident) credentials are listed here. "
            "Standard website logins and SSH keys created without -O resident "
            "are not stored on the device and cannot be enumerated."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        credentials_layout.addWidget(note)

        # Credential actions
        actions_layout = QHBoxLayout()

        self._refresh_button = QPushButton("Refresh Credentials")
        self._refresh_button.clicked.connect(self._refresh_credentials)
        actions_layout.addWidget(self._refresh_button)

        self._rename_button = QPushButton("Rename Credential")
        self._rename_button.clicked.connect(self._rename_credential)
        self._rename_button.setEnabled(False)
        actions_layout.addWidget(self._rename_button)

        self._delete_button = QPushButton("Delete Credential")
        self._delete_button.clicked.connect(self._delete_credential)
        self._delete_button.setEnabled(False)
        actions_layout.addWidget(self._delete_button)

        actions_layout.addStretch()
        credentials_layout.addLayout(actions_layout)

        # PIN Management Group
        pin_group = QGroupBox("PIN Management")
        pin_layout = QVBoxLayout(pin_group)

        # PIN status
        pin_status_layout = QHBoxLayout()
        pin_status_layout.addWidget(QLabel("PIN Status:"))
        self._pin_status_label = QLabel("Unknown")
        pin_status_layout.addWidget(self._pin_status_label)
        pin_status_layout.addStretch()
        pin_layout.addLayout(pin_status_layout)

        # PIN actions
        pin_actions_layout = QHBoxLayout()

        self._change_pin_button = QPushButton("Change PIN")
        self._change_pin_button.clicked.connect(self._change_pin)
        pin_actions_layout.addWidget(self._change_pin_button)

        self._set_pin_button = QPushButton("Set New PIN")
        self._set_pin_button.clicked.connect(self._set_pin)
        pin_actions_layout.addWidget(self._set_pin_button)

        pin_actions_layout.addStretch()
        pin_layout.addLayout(pin_actions_layout)

        self._transport_hint_label = QLabel("")
        self._transport_hint_label.setWordWrap(True)
        self._apply_transport_hint_style()
        self._transport_hint_label.setVisible(False)
        pin_layout.addWidget(self._transport_hint_label)

        hint_actions_layout = QHBoxLayout()
        self._restart_admin_button = QPushButton("Restart as Administrator")
        self._restart_admin_button.clicked.connect(self._restart_as_admin)
        self._restart_admin_button.setVisible(False)
        hint_actions_layout.addWidget(self._restart_admin_button)
        hint_actions_layout.addStretch()
        pin_layout.addLayout(hint_actions_layout)

        # Status section
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))

        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 0)  # Indeterminate progress
        self._status_progress.setVisible(False)

        self._status_label = QLabel("Ready")
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._status_progress)
        status_layout.addStretch()

        # Add groups to main layout
        layout.addWidget(credentials_group)
        layout.addWidget(pin_group)
        layout.addLayout(status_layout)
        layout.addStretch()

        # Connect table selection signal
        self._credentials_table.itemSelectionChanged.connect(self._on_selection_changed)

        # Initially disable buttons
        self._set_buttons_enabled(False)

    def set_device(self, device: SoloDevice) -> None:
        """Set the current device."""
        self._device = device
        self._setup_worker()
        self._set_buttons_enabled(self._worker is not None)
        if getattr(device.mode, "value", None) != "regular":
            self._pin_status_label.setText("Unavailable in bootloader mode")
            self._status_label.setText("FIDO2 unavailable in bootloader mode")
            self._set_transport_hint("")
            return
        # Show the Windows CCID-only hint immediately, even if the async
        # get_info path later fails or is delayed.
        if (
            sys.platform == "win32"
            and hasattr(device, "prefers_ccid")
            and device.prefers_ccid()
        ):
            self._on_pin_status_updated(
                {
                    "ctap2_available": False,
                    "pin_set": False,
                    "pin_retries": None,
                    "uv_set": False,
                    "cred_mgmt_supported": False,
                }
            )
        # _update_pin_status will be called via signal after worker thread starts

    def refresh_state(self) -> None:
        """Re-check live PIN status from the device (e.g. after PIN set externally)."""
        if self._worker:
            self._update_pin_status()

    def clear_device(self) -> None:
        """Clear the current device."""
        self._device = None
        self._cleanup_worker()
        self._credentials = []
        self._credentials_table.setRowCount(0)
        self._pin_status_label.setText("Unknown")
        self._status_label.setText("No device connected")
        self._transport_hint_label.setVisible(False)
        self._restart_admin_button.setVisible(False)
        self._set_buttons_enabled(False)

    def _setup_worker(self) -> None:
        """Setup the FIDO2 worker thread."""
        if not self._device or self._device.mode.value != "regular":
            return

        # Cleanup existing worker
        self._cleanup_worker()

        # Create new worker thread - Fido2Worker uses DeviceManager singleton
        self._worker_thread = QThread()
        self._worker = Fido2Worker()
        self._worker.moveToThread(self._worker_thread)

        # Connect signals
        self._worker.credentials_loaded.connect(self._on_credentials_loaded)
        self._worker.credential_deleted.connect(self._on_credential_deleted)
        self._worker.credential_renamed.connect(self._on_credential_renamed)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.pin_status_updated.connect(self._on_pin_status_updated)
        self._worker.pin_required.connect(self._on_pin_required)
        self._worker.error_occurred.connect(self._on_error_occurred)

        # Start thread
        self._worker_thread.start()

        # Request PIN status after a short delay to ensure worker is ready
        QTimer.singleShot(100, lambda: self._worker.get_pin_status() if self._worker else None)

    def _cleanup_worker(self) -> None:
        """Cleanup the worker thread."""
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable buttons based on device connection."""
        self._refresh_button.setEnabled(enabled)
        self._change_pin_button.setEnabled(enabled)
        self._set_pin_button.setEnabled(enabled)

    def _apply_transport_hint_style(self) -> None:
        """Apply theme-aware styling to the transport warning banner."""
        colors = _get_warning_colors()
        self._transport_hint_label.setStyleSheet(
            f"background-color: {colors['bg']}; "
            f"border: 1px solid {colors['border']}; "
            f"color: {colors['text']}; "
            "padding: 8px; border-radius: 5px;"
        )

    def _set_transport_hint(self, message: str = "", *, show_restart: bool = False) -> None:
        """Show or hide the Windows-specific CTAP HID transport hint."""
        visible = bool(message)
        self._apply_transport_hint_style()
        self._transport_hint_label.setVisible(visible)
        self._transport_hint_label.setText(message)
        self._restart_admin_button.setVisible(visible and show_restart)

    def _restart_as_admin(self) -> None:
        """Restart the GUI with Windows Administrator rights."""
        ok, error = restart_as_admin()
        if ok:
            QApplication.instance().quit()
            return
        QMessageBox.critical(
            self,
            "Restart Failed",
            f"Could not restart the GUI as Administrator:\n{error}",
        )

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Set busy state with progress indicator."""
        self._status_progress.setVisible(busy)
        if busy:
            self._status_label.setText(message)
        else:
            self._status_label.setText("Ready")

    def _on_selection_changed(self) -> None:
        """Handle credential selection change."""
        has_selection = bool(self._credentials_table.selectedItems())
        self._rename_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _refresh_credentials(self) -> None:
        """Refresh the credentials list."""
        if not self._worker:
            return

        if not self._pin_set:
            self._status_label.setText(
                "Set a PIN first to enable credential management"
            )
            return

        self._set_busy(True, "Loading credentials...")
        self._worker.load_credentials()

    def _on_credentials_loaded(self, credentials: List[Fido2Credential]) -> None:
        """Handle credentials loaded from worker."""
        self._set_busy(False)

        # Store credentials for later lookup
        self._credentials = credentials

        # Clear table
        self._credentials_table.setRowCount(0)

        # Add credentials to table
        for i, cred in enumerate(credentials):
            self._credentials_table.insertRow(i)

            # Display name (prefer display name, fall back to user name)
            display_name = cred.user_display_name or cred.user_name or "Unknown"
            self._credentials_table.setItem(i, 0, QTableWidgetItem(display_name))
            self._credentials_table.setItem(i, 1, QTableWidgetItem(cred.rp_id))
            self._credentials_table.setItem(i, 2, QTableWidgetItem(cred.rp_name))
            self._credentials_table.setItem(i, 3, QTableWidgetItem(cred.algorithm))

    def _rename_credential(self) -> None:
        """Rename the selected credential."""
        current_row = self._credentials_table.currentRow()
        if current_row < 0 or current_row >= len(self._credentials):
            return

        credential = self._credentials[current_row]
        current_name = credential.user_display_name or credential.user_name

        new_name, ok = QInputDialog.getText(
            self, "Rename Credential", "New display name:", text=current_name
        )

        if ok and new_name and self._worker:
            self._set_busy(True, "Renaming credential...")
            self._worker.rename_credential(credential, new_name)

    def _delete_credential(self) -> None:
        """Delete the selected credential."""
        current_row = self._credentials_table.currentRow()
        if current_row < 0 or current_row >= len(self._credentials):
            return

        credential = self._credentials[current_row]
        credential_name = credential.user_display_name or credential.user_name

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the credential '{credential_name}' "
            f"for {credential.rp_id}?\n\n"
            "This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes and self._worker:
            self._set_busy(True, "Deleting credential...")
            self._worker.delete_credential(credential)

    def _change_pin(self) -> None:
        """Change the FIDO2 PIN."""
        if not self._worker:
            return

        dialog = ChangePinDialog(self, is_new_pin=False)
        if dialog.exec() == QDialog.Accepted:
            current_pin = dialog.get_current_pin()
            new_pin = dialog.get_new_pin()
            if current_pin and new_pin:
                self._set_busy(True, "Changing PIN...")
                self._worker.change_pin(current_pin, new_pin)

    def _set_pin(self) -> None:
        """Set a new FIDO2 PIN."""
        if not self._worker:
            return

        dialog = ChangePinDialog(self, is_new_pin=True)
        if dialog.exec() == QDialog.Accepted:
            new_pin = dialog.get_new_pin()
            if new_pin:
                self._set_busy(True, "Setting PIN...")
                self._worker.set_new_pin(new_pin)

    def _on_pin_required(self) -> None:
        """Handle PIN required signal from worker."""
        self._set_busy(False)

        dialog = PinDialog(
            self,
            title="PIN Required",
            message="Enter your device PIN to access credentials:",
        )
        if dialog.exec() == QDialog.Accepted:
            pin = dialog.get_pin()
            if pin and self._worker:
                # Pass PIN directly to load_credentials rather than pre-caching it.
                # The worker caches it only on successful verification.
                self._set_busy(True, "Loading credentials...")
                self._worker.load_credentials(pin)
        else:
            self._status_label.setText("PIN entry cancelled")

    def _update_pin_status(self) -> None:
        """Update the PIN status display."""
        if not self._worker:
            return

        # Call get_pin_status on worker
        self._worker.get_pin_status()

    def _on_credential_deleted(self, success: bool, error: str) -> None:
        """Handle credential deletion result."""
        self._set_busy(False)

        if success:
            self._refresh_credentials()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete credential: {error}")

    def _on_credential_renamed(self, success: bool, error: str) -> None:
        """Handle credential rename result."""
        self._set_busy(False)

        if success:
            self._refresh_credentials()
        else:
            QMessageBox.critical(self, "Error", f"Failed to rename credential: {error}")

    def _on_pin_changed(self, success: bool, error: str) -> None:
        """Handle PIN change result."""
        self._set_busy(False)

        if success:
            QMessageBox.information(self, "Success", "PIN changed successfully")
            self._update_pin_status()
        else:
            QMessageBox.critical(self, "Error", f"Failed to change PIN: {error}")

    def _on_pin_status_updated(self, status: dict) -> None:
        """Handle PIN status update."""
        ctap2_available = status.get("ctap2_available", True)
        pin_set = status.get("pin_set", False)
        retries = status.get("pin_retries")
        cred_mgmt = status.get("cred_mgmt_supported", False)

        if not ctap2_available:
            self._pin_status_label.setText("CTAP HID not available")
            self._change_pin_button.setEnabled(False)
            self._set_pin_button.setEnabled(False)
            self._refresh_button.setEnabled(False)
            self._rename_button.setEnabled(False)
            self._delete_button.setEnabled(False)
            self._pin_set = False
            self._status_label.setText("FIDO2 requires the HID interface, but Windows only exposed CCID")
            if can_restart_as_admin():
                self._set_transport_hint(
                    "Windows exposed only the smartcard (CCID) interface for this token. "
                    "Try restarting the GUI as Administrator so the FIDO2 HID interface "
                    "can be enumerated as well.",
                    show_restart=True,
                )
            elif is_windows_admin():
                self._set_transport_hint(
                    "The GUI is already running as Administrator, but Windows still did not "
                    "expose the SoloKeys FIDO2 HID interface. FIDO2 PIN and credential "
                    "management will stay unavailable until the HID interface appears.",
                    show_restart=False,
                )
            else:
                self._set_transport_hint(
                    "FIDO2 needs the CTAP HID interface, but only CCID is currently visible.",
                    show_restart=False,
                )
            return

        self._set_transport_hint()

        # Update PIN status display
        if pin_set:
            if retries is not None:
                self._pin_status_label.setText(f"Set ({retries} retries remaining)")
            else:
                self._pin_status_label.setText("Set")
        else:
            self._pin_status_label.setText("Not set")

        # Update button states based on PIN status
        self._change_pin_button.setEnabled(pin_set)
        self._set_pin_button.setEnabled(not pin_set)

        self._pin_set = pin_set
        # Refresh is enabled whenever the device supports credential management,
        # regardless of PIN state (no-PIN case is handled in _refresh_credentials).
        self._refresh_button.setEnabled(cred_mgmt)
        if not cred_mgmt:
            self._status_label.setText("Credential management not supported")

    def _on_error_occurred(self, error: str) -> None:
        """Handle worker error."""
        self._set_busy(False)
        # PIN not set is informational, not an error
        if "PIN not set" in error:
            QMessageBox.information(self, "PIN Required", error)
        else:
            QMessageBox.critical(self, "Error", error)
