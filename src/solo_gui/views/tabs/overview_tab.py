"""Overview tab for SoloKeys GUI."""

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QGridLayout, QPushButton,
    QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QThread

from solo_gui.models.device import SoloDevice, DeviceInfo, format_firmware_full
from solo_gui.workers.firmware_worker import FirmwareUpdateWorker, FirmwareInfo


class OverviewTab(QWidget):
    """Overview tab showing device status and firmware update."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._firmware_worker: Optional[FirmwareUpdateWorker] = None
        self._firmware_thread: Optional[QThread] = None
        self._firmware_info: Optional[FirmwareInfo] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Device Information
        info_group = QGroupBox("Device Information")
        info_layout = QGridLayout(info_group)

        self._device_type_label = QLabel("Not connected")
        self._firmware_label = QLabel("-")

        info_layout.addWidget(QLabel("Device:"), 0, 0)
        info_layout.addWidget(self._device_type_label, 0, 1)
        info_layout.addWidget(QLabel("Firmware:"), 1, 0)
        info_layout.addWidget(self._firmware_label, 1, 1)
        info_layout.setColumnStretch(1, 1)

        # Firmware Update
        firmware_group = QGroupBox("Firmware Update")
        firmware_layout = QVBoxLayout(firmware_group)

        self._check_updates_button = QPushButton("Check for Firmware Updates")
        self._check_updates_button.clicked.connect(self._check_firmware_updates)
        firmware_layout.addWidget(self._check_updates_button)

        self._update_info_label = QLabel("")
        self._update_info_label.setWordWrap(True)
        firmware_layout.addWidget(self._update_info_label)

        self._download_update_button = QPushButton("Download and Install Update")
        self._download_update_button.clicked.connect(self._start_firmware_update)
        self._download_update_button.setVisible(False)
        firmware_layout.addWidget(self._download_update_button)

        # Status
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 100)
        self._status_progress.setVisible(False)
        self._status_label = QLabel("Ready")
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._status_progress)
        status_layout.addStretch()

        layout.addWidget(info_group)
        layout.addWidget(firmware_group)
        layout.addLayout(status_layout)
        layout.addStretch()

        self._check_updates_button.setEnabled(False)

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self._firmware_info = None
        self._setup_firmware_worker()
        self._update_device_info()
        self._check_updates_button.setEnabled(True)

    def clear_device(self) -> None:
        self._device = None
        self._firmware_info = None
        self._cleanup_firmware_worker()
        self._clear_device_info()
        self._check_updates_button.setEnabled(False)

    def _setup_firmware_worker(self) -> None:
        self._cleanup_firmware_worker()
        if not self._device:
            return
        self._firmware_thread = QThread()
        self._firmware_worker = FirmwareUpdateWorker(self._device)
        self._firmware_worker.moveToThread(self._firmware_thread)
        self._firmware_worker.update_progress.connect(self._on_firmware_progress)
        self._firmware_worker.firmware_info_found.connect(self._on_firmware_info)
        self._firmware_worker.update_completed.connect(self._on_update_completed)
        self._firmware_worker.error_occurred.connect(self._on_firmware_error)
        self._firmware_thread.start()

    def _cleanup_firmware_worker(self) -> None:
        if self._firmware_thread:
            self._firmware_thread.quit()
            self._firmware_thread.wait()
            self._firmware_thread = None
            self._firmware_worker = None

    def _update_device_info(self) -> None:
        if not self._device:
            self._clear_device_info()
            return
        info = self._device.get_info()
        self._device_type_label.setText(info.serial_number or "SoloKeys Solo 2")
        self._firmware_label.setText(format_firmware_full(info.firmware_version))

    def _clear_device_info(self) -> None:
        self._device_type_label.setText("Not connected")
        self._firmware_label.setText("-")
        self._update_info_label.setText("")
        self._download_update_button.setVisible(False)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._status_progress.setVisible(busy)
        self._status_label.setText(message if busy else "Ready")

    def _check_firmware_updates(self) -> None:
        if not self._device or not self._firmware_worker:
            return
        self._set_busy(True, "Checking for updates...")
        info = self._device.get_info()
        self._firmware_worker.check_for_updates(info.firmware_version or "0")

    def _start_firmware_update(self) -> None:
        if not self._firmware_worker or not self._firmware_info:
            return
        reply = QMessageBox.warning(
            self,
            "Confirm Firmware Update",
            f"Update firmware to version {self._firmware_info.version}?\n\n"
            "The device will reboot to bootloader mode during the update.\n"
            "Do not disconnect the device during this process.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._set_busy(True, "Starting firmware update...")
            self._check_updates_button.setEnabled(False)
            self._firmware_worker.perform_update(self._firmware_info)

    def _on_firmware_progress(self, progress: int, message: str) -> None:
        self._status_progress.setVisible(True)
        self._status_progress.setValue(progress)
        self._status_label.setText(message)

    def _on_firmware_info(self, firmware_info: object) -> None:
        self._set_busy(False)
        if firmware_info:
            self._firmware_info = firmware_info
            self._update_info_label.setText(
                f"Update available: v{firmware_info.version} "
                f"({firmware_info.build_date})\n"
                f"{firmware_info.size // 1024} KB"
            )
            self._download_update_button.setVisible(True)
        else:
            self._firmware_info = None
            self._update_info_label.setText("Firmware is up to date.")
            self._download_update_button.setVisible(False)

    def _on_update_completed(self, success: bool, message: str) -> None:
        self._set_busy(False)
        self._check_updates_button.setEnabled(True)
        if success:
            QMessageBox.information(self, "Update Complete", message)
        else:
            QMessageBox.critical(self, "Update Failed", message)

    def _on_firmware_error(self, error: str) -> None:
        self._set_busy(False)
        self._check_updates_button.setEnabled(True)
        self._status_label.setText(f"Error: {error}")
        QMessageBox.critical(self, "Firmware Error", error)
