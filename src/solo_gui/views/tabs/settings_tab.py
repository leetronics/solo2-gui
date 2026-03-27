"""Settings tab for SoloKeys GUI — diagnostics and application settings."""

from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QCheckBox,
    QGridLayout,
    QTextEdit,
    QTabWidget,
)
from PySide6.QtCore import Qt, QThread

from solo_gui.models.device import SoloDevice, format_firmware_full
from solo_gui.workers.admin_worker import AdminWorker, DeviceDiagnostics


class SettingsTab(QWidget):
    """Settings tab — device diagnostics and application preferences."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._admin_worker: Optional[AdminWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._diagnostics: Optional[DeviceDiagnostics] = None
        self._settings_tabs: Optional[QTabWidget] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._settings_tabs = QTabWidget()

        self._settings_tabs.addTab(self._create_diagnostics_tab(), "Diagnostics")
        self._settings_tabs.addTab(self._create_app_settings_tab(), "Application")

        layout.addWidget(self._settings_tabs)

        # Status bar
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 100)
        self._status_progress.setVisible(False)
        self._status_progress.setMaximumWidth(200)
        self._status_label = QLabel("No device connected")
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._status_progress)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        self._set_controls_enabled(False)

    def _create_diagnostics_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Device Info
        info_group = QGroupBox("Device Information")
        info_layout = QGridLayout(info_group)
        self._uuid_label = QLabel("-")
        self._firmware_label = QLabel("-")
        info_layout.addWidget(QLabel("UUID:"), 0, 0)
        info_layout.addWidget(self._uuid_label, 0, 1)
        info_layout.addWidget(QLabel("Firmware:"), 1, 0)
        info_layout.addWidget(self._firmware_label, 1, 1)
        info_layout.setColumnStretch(1, 1)

        # Status
        status_group = QGroupBox("Status")
        status_layout = QGridLayout(status_group)
        self._locked_label = QLabel("-")
        status_layout.addWidget(QLabel("Locked:"), 0, 0)
        status_layout.addWidget(self._locked_label, 0, 1)
        status_layout.setColumnStretch(1, 1)

        # CTAP2 Options
        ctap2_group = QGroupBox("CTAP2 Options")
        ctap2_layout = QVBoxLayout(ctap2_group)
        self._ctap2_options_text = QTextEdit()
        self._ctap2_options_text.setReadOnly(True)
        self._ctap2_options_text.setMaximumHeight(100)
        ctap2_layout.addWidget(self._ctap2_options_text)

        # Device Capabilities (moved from Overview)
        caps_group = QGroupBox("Device Capabilities")
        caps_layout = QVBoxLayout(caps_group)
        self._capabilities_text = QTextEdit()
        self._capabilities_text.setReadOnly(True)
        self._capabilities_text.setMaximumHeight(80)
        caps_layout.addWidget(self._capabilities_text)

        # Refresh
        btn_layout = QHBoxLayout()
        self._refresh_diagnostics_btn = QPushButton("Refresh Diagnostics")
        self._refresh_diagnostics_btn.clicked.connect(self._refresh_diagnostics)
        btn_layout.addWidget(self._refresh_diagnostics_btn)
        btn_layout.addStretch()

        layout.addWidget(info_group)
        layout.addWidget(status_group)
        layout.addWidget(ctap2_group)
        layout.addWidget(caps_group)
        layout.addLayout(btn_layout)
        layout.addStretch()
        return widget

    def _create_app_settings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        refresh_group = QGroupBox("Auto-refresh Settings")
        refresh_layout = QVBoxLayout(refresh_group)
        self._auto_refresh_checkbox = QCheckBox("Auto-refresh device status")
        self._auto_refresh_checkbox.setChecked(True)
        refresh_layout.addWidget(self._auto_refresh_checkbox)

        notification_group = QGroupBox("Notifications")
        notification_layout = QVBoxLayout(notification_group)
        self._notifications_checkbox = QCheckBox("Show notifications for device events")
        self._notifications_checkbox.setChecked(True)
        notification_layout.addWidget(self._notifications_checkbox)
        self._sound_checkbox = QCheckBox("Play sounds for touch prompts")
        self._sound_checkbox.setChecked(False)
        notification_layout.addWidget(self._sound_checkbox)

        security_group = QGroupBox("Security")
        security_layout = QVBoxLayout(security_group)
        self._clear_pin_checkbox = QCheckBox("Clear cached PINs on app minimize")
        self._clear_pin_checkbox.setChecked(True)
        security_layout.addWidget(self._clear_pin_checkbox)
        self._confirm_delete_checkbox = QCheckBox("Confirm before deleting credentials")
        self._confirm_delete_checkbox.setChecked(True)
        security_layout.addWidget(self._confirm_delete_checkbox)

        layout.addWidget(refresh_group)
        layout.addWidget(notification_group)
        layout.addWidget(security_group)
        layout.addStretch()
        return widget

    # -------------------------------------------------------------------------

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self._setup_worker()
        self._set_controls_enabled(True)
        self._status_label.setText("Device connected")
        self._refresh_diagnostics()
        # Populate capabilities from device info
        info = device.get_info()
        if info.capabilities:
            self._capabilities_text.setPlainText(", ".join(info.capabilities))
        else:
            self._capabilities_text.setPlainText("No capabilities information available")

    def clear_device(self) -> None:
        self._cleanup_worker()
        self._device = None
        self._diagnostics = None
        self._set_controls_enabled(False)
        self._clear_diagnostics_display()
        self._status_label.setText("No device connected")

    def _setup_worker(self) -> None:
        self._cleanup_worker()
        if not self._device:
            return
        self._worker_thread = QThread()
        self._admin_worker = AdminWorker(self._device)
        self._admin_worker.moveToThread(self._worker_thread)
        self._admin_worker.operation_started.connect(self._on_operation_started)
        self._admin_worker.operation_progress.connect(self._on_operation_progress)
        self._admin_worker.operation_completed.connect(self._on_operation_completed)
        self._admin_worker.error_occurred.connect(self._on_error)
        self._admin_worker.diagnostics_ready.connect(self._on_diagnostics_ready)
        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
        self._admin_worker = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._refresh_diagnostics_btn.setEnabled(enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._status_progress.setVisible(busy)
        self._status_label.setText(message if busy else "Ready")

    def _clear_diagnostics_display(self) -> None:
        self._uuid_label.setText("-")
        self._firmware_label.setText("-")
        self._locked_label.setText("-")
        self._ctap2_options_text.setPlainText("")
        self._capabilities_text.setPlainText("")

    def _refresh_diagnostics(self) -> None:
        if self._admin_worker:
            self._admin_worker.get_diagnostics()

    # Worker slots

    def _on_operation_started(self, name: str) -> None:
        self._set_busy(True, name)
        self._status_progress.setValue(0)

    def _on_operation_progress(self, progress: int, message: str) -> None:
        self._status_progress.setValue(progress)
        self._status_label.setText(message)

    def _on_operation_completed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        self._status_label.setText(message if success else f"Failed: {message}")

    def _on_error(self, error: str) -> None:
        self._set_busy(False)
        self._status_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Error", error)

    def _on_diagnostics_ready(self, diagnostics: DeviceDiagnostics) -> None:
        self._diagnostics = diagnostics
        d = diagnostics
        self._uuid_label.setText(d.uuid or "-")
        self._firmware_label.setText(
            format_firmware_full(d.firmware_version) if d.firmware_version else "-"
        )
        self._locked_label.setText("Yes" if d.is_locked else "No")
        if d.ctap2_options:
            self._ctap2_options_text.setPlainText(
                "\n".join(f"{k}: {v}" for k, v in sorted(d.ctap2_options.items()))
            )
        else:
            self._ctap2_options_text.setPlainText("(none)")
