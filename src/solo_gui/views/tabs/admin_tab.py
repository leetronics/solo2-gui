"""Admin tab for SoloKeys GUI — device management and advanced operations."""

from functools import partial
import os
import sys
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
    QLineEdit,
    QFormLayout,
    QFileDialog,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication

from solo_gui.models.device import SoloDevice
from solo_gui.utils.windows_elevation import (
    can_restart_as_admin,
    is_windows_admin,
    restart_as_admin_from_ui,
)
from solo_gui.workers.admin_worker import AdminWorker, RebootMode


def _is_dark_mode() -> bool:
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _get_danger_zone_colors() -> dict:
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


def _get_warning_colors() -> dict:
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


class AdminTab(QWidget):
    """Admin tab for Solo2 admin app operations and hacker-variant provisioning."""

    reconnect_expected = Signal()

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._admin_worker: Optional[AdminWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Quick Actions
        quick_group = QGroupBox("Quick Actions")
        quick_layout = QHBoxLayout(quick_group)
        self._wink_btn = QPushButton("Wink Device")
        self._wink_btn.setToolTip("Flash the device LED to identify it")
        self._wink_btn.clicked.connect(self._wink_device)
        quick_layout.addWidget(self._wink_btn)
        quick_layout.addStretch()
        layout.addWidget(quick_group)

        # Reboot
        reboot_group = QGroupBox("Device Reboot")
        reboot_layout = QVBoxLayout(reboot_group)
        reboot_layout.addWidget(QLabel("Rebooting to bootloader mode is required for firmware updates."))
        reboot_btn_layout = QHBoxLayout()
        self._reboot_regular_btn = QPushButton("Reboot (Normal)")
        self._reboot_regular_btn.clicked.connect(lambda: self._reboot(RebootMode.REGULAR))
        reboot_btn_layout.addWidget(self._reboot_regular_btn)
        self._reboot_bootloader_btn = QPushButton("Reboot to Bootloader")
        self._reboot_bootloader_btn.clicked.connect(lambda: self._reboot(RebootMode.BOOTLOADER))
        self._reboot_bootloader_btn.setStyleSheet("QPushButton { color: orange; }")
        reboot_btn_layout.addWidget(self._reboot_bootloader_btn)
        reboot_btn_layout.addStretch()
        reboot_layout.addLayout(reboot_btn_layout)
        layout.addWidget(reboot_group)

        # Danger Zone
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
        danger_info = QLabel(
            "Factory reset clears FIDO2 credentials and PIN state, and resets the "
            "Secrets/Vault app. It does not reset PIV or OpenPGP data.\n\n"
            "To use the reset window, unplug and re-plug the device, start reset within "
            "about 10 seconds, then touch the device when prompted."
        )
        danger_info.setWordWrap(True)
        danger_info.setStyleSheet(f"color: {danger_colors['text']};")
        danger_layout.addWidget(danger_info)

        self._factory_reset_hint_label = QLabel("")
        self._factory_reset_hint_label.setWordWrap(True)
        self._apply_factory_reset_hint_style()
        self._factory_reset_hint_label.setVisible(False)
        danger_layout.addWidget(self._factory_reset_hint_label)

        danger_btn_layout = QHBoxLayout()
        self._factory_reset_btn = QPushButton("Factory Reset")
        self._factory_reset_btn.setToolTip(
            "Unplug and re-plug the device, start reset within about 10 seconds, then touch to confirm."
        )
        self._factory_reset_btn.clicked.connect(self._factory_reset)
        self._factory_reset_btn.setStyleSheet(f"""
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
        danger_btn_layout.addWidget(self._factory_reset_btn)
        self._restart_admin_button = QPushButton("Restart as Administrator")
        self._restart_admin_button.clicked.connect(partial(restart_as_admin_from_ui, self))
        self._restart_admin_button.setVisible(False)
        danger_btn_layout.addWidget(self._restart_admin_button)
        danger_btn_layout.addStretch()
        danger_layout.addLayout(danger_btn_layout)
        layout.addWidget(danger_group)
        layout.addStretch()

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

    # -------------------------------------------------------------------------

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self._setup_worker()
        caps = device.capabilities
        self._apply_capabilities(caps)
        self._status_label.setText("Device connected")

    def clear_device(self) -> None:
        self._cleanup_worker()
        self._device = None
        self._set_controls_enabled(False)
        self._status_label.setText("No device connected")
        self._set_factory_reset_hint("")

    # -------------------------------------------------------------------------

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
        self._admin_worker.device_disconnected.connect(self._on_device_disconnected)
        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
        self._admin_worker = None


    def _set_controls_enabled(self, enabled: bool) -> None:
        self._wink_btn.setEnabled(enabled)
        self._reboot_regular_btn.setEnabled(enabled)
        self._reboot_bootloader_btn.setEnabled(enabled)
        self._update_factory_reset_controls(enabled)

    def _apply_capabilities(self, caps) -> None:
        has_device = caps is not None
        self._wink_btn.setEnabled(has_device)
        self._reboot_regular_btn.setEnabled(has_device and caps.has_reboot)
        self._reboot_bootloader_btn.setEnabled(has_device and caps.has_boot_to_bootloader)
        self._update_factory_reset_controls(has_device)

    def _apply_factory_reset_hint_style(self) -> None:
        colors = _get_warning_colors()
        self._factory_reset_hint_label.setStyleSheet(
            f"background-color: {colors['bg']}; "
            f"border: 1px solid {colors['border']}; "
            f"color: {colors['text']}; "
            "padding: 8px; border-radius: 5px;"
        )

    def _set_factory_reset_hint(self, message: str = "", *, show_restart: bool = False) -> None:
        visible = bool(message)
        self._apply_factory_reset_hint_style()
        self._factory_reset_hint_label.setVisible(visible)
        self._factory_reset_hint_label.setText(message)
        self._restart_admin_button.setVisible(visible and show_restart)

    def _update_factory_reset_controls(self, base_enabled: bool) -> None:
        message = ""
        show_restart = False
        transport_ready = True

        if (
            base_enabled
            and self._device is not None
            and getattr(self._device.mode, "value", None) == "regular"
            and sys.platform == "win32"
            and hasattr(self._device, "prefers_ccid")
            and self._device.prefers_ccid()
        ):
            transport_ready = False
            if can_restart_as_admin():
                message = (
                    "Factory reset needs the FIDO2 HID interface, but Windows only exposed "
                    "CCID for this token. Restart the GUI as Administrator, then retry reset."
                )
                show_restart = True
            elif is_windows_admin():
                message = (
                    "Factory reset needs the FIDO2 HID interface, but Windows still exposed "
                    "only CCID for this token. Reset stays unavailable until the HID interface appears."
                )
            else:
                message = (
                    "Factory reset needs the FIDO2 HID interface, but Windows only exposed "
                    "CCID for this token."
                )

        self._factory_reset_btn.setEnabled(base_enabled and transport_ready)
        self._set_factory_reset_hint(message, show_restart=show_restart)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._status_progress.setVisible(busy)
        self._status_label.setText(message if busy else "Ready")

    # -------------------------------------------------------------------------
    # Actions

    def _wink_device(self) -> None:
        if self._admin_worker:
            self._admin_worker.wink()

    def _reboot(self, mode: RebootMode) -> None:
        mode_name = "bootloader" if mode == RebootMode.BOOTLOADER else "normal"
        reply = QMessageBox.question(
            self,
            "Confirm Reboot",
            f"Reboot device to {mode_name} mode?\n\nThe device will disconnect temporarily.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes and self._admin_worker:
            self.reconnect_expected.emit()
            self._admin_worker.reboot(mode)

    def _factory_reset(self) -> None:
        if not self._factory_reset_btn.isEnabled():
            if self._factory_reset_hint_label.isVisible():
                QMessageBox.warning(
                    self,
                    "Factory Reset Unavailable",
                    self._factory_reset_hint_label.text(),
                )
            return
        reply = QMessageBox.warning(
            self,
            "Confirm Factory Reset",
            "WARNING: This will permanently reset:\n\n"
            "- All FIDO2 credentials\n"
            "- FIDO2 PIN state\n"
            "- All Secrets/Vault credentials\n"
            "- Secrets/Vault PIN state\n\n"
            "PIV and OpenPGP data are not reset by this action.\n\n"
            "This action CANNOT be undone!\n\n"
            "Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            reply2 = QMessageBox.critical(
                self,
                "Final Confirmation",
                "FINAL WARNING: FIDO2 and Secrets/Vault data will be reset.\n\n"
                "To use the reset window:\n"
                "1. Unplug and re-plug the device.\n"
                "2. Click Yes within about 10 seconds.\n"
                "3. Touch the device immediately when prompted.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply2 == QMessageBox.Yes and self._admin_worker:
                self._admin_worker.factory_reset(confirm=True)

    # -------------------------------------------------------------------------
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

    def _on_device_disconnected(self) -> None:
        QMessageBox.information(
            self,
            "Device Disconnected",
            "The device has disconnected. It may be rebooting or entering a different mode."
        )
