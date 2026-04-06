"""Overview tab for SoloKeys GUI."""

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGroupBox, QGridLayout, QPushButton,
    QProgressBar, QMessageBox, QLineEdit, QFileDialog, QPlainTextEdit
)
from PySide6.QtCore import Qt, QThread, Signal

from solo_gui.models.device import SoloDevice, DeviceInfo, format_firmware_full
from solo_gui.workers.firmware_worker import FirmwareUpdateWorker, FirmwareInfo


class OverviewTab(QWidget):
    """Overview tab showing device status and firmware update."""

    check_variant_requested = Signal()

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._firmware_worker: Optional[FirmwareUpdateWorker] = None
        self._firmware_thread: Optional[QThread] = None
        self._firmware_info: Optional[FirmwareInfo] = None
        self._isp_variant: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Device Information
        info_group = QGroupBox("Device Information")
        info_vbox = QVBoxLayout(info_group)

        info_grid = QGridLayout()
        self._device_type_label = QLabel("Not connected")
        self._firmware_label = QLabel("-")
        info_grid.addWidget(QLabel("Device:"), 0, 0)
        info_grid.addWidget(self._device_type_label, 0, 1)
        info_grid.addWidget(QLabel("Firmware:"), 1, 0)
        info_grid.addWidget(self._firmware_label, 1, 1)
        info_grid.setColumnStretch(1, 1)
        info_vbox.addLayout(info_grid)

        check_row = QHBoxLayout()
        self._check_variant_btn = QPushButton("Check Variant")
        self._check_variant_btn.setToolTip(
            "Probe the hardware to confirm the device variant and lock state.\n"
            "The device will reboot to bootloader mode — touch the button when prompted."
        )
        self._check_variant_btn.clicked.connect(self.check_variant_requested)
        self._check_variant_btn.setEnabled(False)
        check_row.addWidget(self._check_variant_btn)
        check_row.addStretch()
        info_vbox.addLayout(check_row)

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

        # Flash from File
        self._flash_group = QGroupBox("Flash Firmware from File")
        flash_layout = QVBoxLayout(self._flash_group)
        file_row = QHBoxLayout()
        self._flash_file_input = QLineEdit()
        self._flash_file_input.setPlaceholderText("Path to firmware file (.bin or .sb2)…")
        file_row.addWidget(self._flash_file_input)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_firmware_file)
        file_row.addWidget(browse_btn)
        flash_layout.addLayout(file_row)
        self._flash_file_btn = QPushButton("Flash File")
        self._flash_file_btn.clicked.connect(self._flash_from_file)
        flash_layout.addWidget(self._flash_file_btn)

        # Flash log + progress (hidden until a flash operation starts)
        self._log_group = QGroupBox("Flash Output")
        log_layout = QVBoxLayout(self._log_group)
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setFixedHeight(110)
        log_layout.addWidget(self._log_area)
        self._status_progress = QProgressBar()
        self._status_progress.setRange(0, 100)
        self._status_progress.setVisible(False)
        log_layout.addWidget(self._status_progress)

        layout.addWidget(info_group)
        layout.addWidget(firmware_group)
        layout.addWidget(self._flash_group)
        layout.addWidget(self._log_group)
        layout.addStretch()

        self._check_updates_button.setEnabled(False)
        self._flash_file_btn.setEnabled(False)
        self._flash_group.setVisible(False)
        self._log_group.setVisible(False)

    def set_device(self, device: SoloDevice) -> None:
        self._device = device
        self._isp_variant = None
        self._firmware_info = None
        self._setup_firmware_worker()
        self._update_device_info()
        self._check_updates_button.setEnabled(True)
        self._check_variant_btn.setEnabled(True)
        self._update_flash_file_visibility()

    def clear_device(self) -> None:
        self._device = None
        self._isp_variant = None
        self._firmware_info = None
        self._cleanup_firmware_worker()
        self._clear_device_info()
        self._check_updates_button.setEnabled(False)
        self._check_variant_btn.setEnabled(False)
        self._flash_file_btn.setEnabled(False)
        self._flash_group.setVisible(False)

    def on_variant_detected(self, result: str) -> None:
        """Receive ISP variant result from admin_tab and refresh device info."""
        self._isp_variant = result
        self._update_device_info()

    def _setup_firmware_worker(self) -> None:
        self._cleanup_firmware_worker()
        if not self._device:
            return
        self._firmware_thread = QThread()
        variant = getattr(self._device, "variant", "")
        self._firmware_worker = FirmwareUpdateWorker(self._device, variant=variant)
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
        if self._isp_variant == "Hacker (unlocked)":
            variant_label = " (unlocked)"
        elif self._isp_variant == "Hacker (locked)":
            variant_label = " (locked)"
        elif self._isp_variant == "Secure":
            variant_label = " (Secure)"
        else:
            fw = getattr(self._device, "variant", "")
            if fw == "Hacker":
                variant_label = " (unlocked)"
            elif fw == "Secure":
                variant_label = " (locked)"
            else:
                variant_label = f" ({fw})" if fw else ""
        self._device_type_label.setText(f"Solo 2{variant_label}")
        self._firmware_label.setText(format_firmware_full(info.firmware_version))
        self._update_flash_file_visibility()

    def _update_flash_file_visibility(self) -> None:
        variant = getattr(self._device, "variant", "") if self._device else ""
        show_flash_from_file = variant == "Hacker"
        self._flash_group.setVisible(show_flash_from_file)
        self._flash_file_btn.setEnabled(show_flash_from_file and self._device is not None)
        if not show_flash_from_file:
            self._flash_file_input.clear()

    def _clear_device_info(self) -> None:
        self._device_type_label.setText("Not connected")
        self._firmware_label.setText("-")
        self._update_info_label.setText("")
        self._download_update_button.setVisible(False)
        self._flash_group.setVisible(False)
        self._flash_file_input.clear()
        self._status_progress.setVisible(False)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._status_progress.setVisible(busy)
        if busy and message:
            self._log_group.setVisible(True)
            self._log_area.clear()
            self._log_area.appendPlainText(message)

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
            "The device will reboot to bootloader mode during the update. "
            "Touch the device button when prompted to confirm the reboot.\n\n"
            "Do not disconnect the device during this process.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._set_busy(True, "Starting firmware update...")
            self._check_updates_button.setEnabled(False)
            self._firmware_worker.perform_update(self._firmware_info)

    def _browse_firmware_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Firmware File", "", "Firmware (*.bin *.sb2);;All Files (*)"
        )
        if path:
            self._flash_file_input.setText(path)

    def _flash_from_file(self) -> None:
        if not self._firmware_worker:
            return
        path = self._flash_file_input.text().strip()
        if not path:
            QMessageBox.warning(self, "No File", "Select a firmware .bin file first.")
            return
        reply = QMessageBox.warning(
            self,
            "Flash Firmware",
            f"Flash firmware from:\n{path}\n\n"
            "The device will reboot to bootloader mode. "
            "Touch the device button when prompted to confirm the reboot.\n\n"
            "Do not disconnect during the process.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._set_busy(True, "Flashing firmware…")
            self._check_updates_button.setEnabled(False)
            self._flash_file_btn.setEnabled(False)
            self._firmware_worker.flash_from_file(path)

    def _on_firmware_progress(self, progress: int, message: str) -> None:
        self._status_progress.setVisible(True)
        self._status_progress.setValue(progress)
        self._log_area.appendPlainText(message)
        self._log_area.verticalScrollBar().setValue(
            self._log_area.verticalScrollBar().maximum()
        )

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
        self._check_updates_button.setEnabled(self._device is not None)
        self._flash_file_btn.setEnabled(self._device is not None)
        self._log_area.appendPlainText(message)
        if success:
            QMessageBox.information(self, "Update Complete", message)
        else:
            QMessageBox.critical(self, "Update Failed", message)

    def _on_firmware_error(self, error: str) -> None:
        self._set_busy(False)
        self._check_updates_button.setEnabled(self._device is not None)
        self._flash_file_btn.setEnabled(self._device is not None)
        self._log_area.appendPlainText(f"Error: {error}")
        QMessageBox.critical(self, "Firmware Error", error)
