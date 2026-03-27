"""Admin tab for SoloKeys GUI — device management and advanced operations."""

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
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QGuiApplication

from solo_gui.models.device import SoloDevice
from solo_gui.workers.admin_worker import AdminWorker, RebootMode


class AdminTab(QWidget):
    """Admin tab for Solo2 admin app operations and hacker-variant provisioning."""

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
        danger_group = QGroupBox("Danger Zone")
        danger_group.setStyleSheet("QGroupBox { color: red; }")
        danger_layout = QVBoxLayout(danger_group)
        danger_info = QLabel(
            "Factory reset will erase ALL data including credentials, keys, and settings. "
            "This action cannot be undone."
        )
        danger_info.setWordWrap(True)
        danger_info.setStyleSheet("color: gray;")
        danger_layout.addWidget(danger_info)
        danger_btn_layout = QHBoxLayout()
        self._factory_reset_btn = QPushButton("Factory Reset")
        self._factory_reset_btn.clicked.connect(self._factory_reset)
        self._factory_reset_btn.setStyleSheet("QPushButton { color: red; font-weight: bold; }")
        danger_btn_layout.addWidget(self._factory_reset_btn)
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
        self._factory_reset_btn.setEnabled(enabled)

    def _apply_capabilities(self, caps) -> None:
        has_device = caps is not None
        self._wink_btn.setEnabled(has_device)
        self._reboot_regular_btn.setEnabled(has_device and caps.has_reboot)
        self._reboot_bootloader_btn.setEnabled(has_device and caps.has_boot_to_bootloader)
        self._factory_reset_btn.setEnabled(has_device)

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
            self._admin_worker.reboot(mode)

    def _factory_reset(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Confirm Factory Reset",
            "WARNING: This will PERMANENTLY ERASE all data on the device:\n\n"
            "- All FIDO2 credentials\n"
            "- All keys and settings\n\n"
            "This action CANNOT be undone!\n\n"
            "Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            reply2 = QMessageBox.critical(
                self,
                "Final Confirmation",
                "FINAL WARNING: All data will be erased.\n\n"
                "The device will reboot automatically, then you will have\n"
                "~10 seconds to TOUCH it to confirm the reset.\n\n"
                "Be ready to touch the device immediately after clicking Yes.",
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

