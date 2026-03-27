"""Hacker tab for SoloKeys GUI — advanced operations for Hacker variant devices."""

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
from solo_gui.workers.provision_worker import ProvisionWorker


class HackerTab(QWidget):
    """Hacker tab for Solo2 Hacker variant advanced provisioning operations."""

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._provision_worker: Optional[ProvisionWorker] = None
        self._provision_thread: Optional[QThread] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Warning header
        warning_group = QGroupBox("Warning")
        warning_group.setStyleSheet(
            "QGroupBox { border: 2px solid #cc0000; color: #cc0000; }"
        )
        warning_layout = QVBoxLayout(warning_group)
        warning = QLabel(
            "WARNING: These operations are for hardware developers only. "
            "Incorrect use can permanently brick your device."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background-color: #fff3cd; color: #856404; padding: 8px; border-radius: 4px;"
        )
        warning_layout.addWidget(warning)
        layout.addWidget(warning_group)

        # Attestation Keys
        keys_group = QGroupBox("Attestation Keys")
        keys_layout = QVBoxLayout(keys_group)
        for key_type, label in [("ed25519", "Ed25519"), ("p256", "P-256"), ("x25519", "X25519")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label}:"))
            pub_field = QLineEdit()
            pub_field.setReadOnly(True)
            pub_field.setPlaceholderText("(not yet generated)")
            pub_field.setObjectName(f"pubkey_{key_type}")
            row.addWidget(pub_field)
            copy_btn = QPushButton("Copy")
            copy_btn.setMaximumWidth(60)
            copy_btn.clicked.connect(
                lambda _, f=pub_field: QGuiApplication.clipboard().setText(f.text())
            )
            row.addWidget(copy_btn)
            gen_btn = QPushButton("Generate")
            gen_btn.setObjectName(f"gen_{key_type}")
            gen_btn.clicked.connect(lambda _, kt=key_type: self._provision_generate_key(kt))
            row.addWidget(gen_btn)
            keys_layout.addLayout(row)
        layout.addWidget(keys_group)

        # Certificate Storage
        certs_group = QGroupBox("Certificate Storage")
        certs_layout = QVBoxLayout(certs_group)
        for key_type, label in [("ed25519", "Ed25519"), ("p256", "P-256"), ("x25519", "X25519")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label} Cert:"))
            btn = QPushButton("Import…")
            btn.setObjectName(f"import_cert_{key_type}")
            btn.clicked.connect(lambda _, kt=key_type: self._provision_import_cert(kt))
            row.addWidget(btn)
            row.addStretch()
            certs_layout.addLayout(row)
        t1_row = QHBoxLayout()
        t1_row.addWidget(QLabel("T1 Intermediate Pubkey (32 bytes):"))
        t1_btn = QPushButton("Import…")
        t1_btn.setObjectName("import_t1")
        t1_btn.clicked.connect(self._provision_import_t1_pubkey)
        t1_row.addWidget(t1_btn)
        t1_row.addStretch()
        certs_layout.addLayout(t1_row)
        layout.addWidget(certs_group)

        # Write File
        write_group = QGroupBox("Write File")
        write_layout = QFormLayout(write_group)
        self._provision_path_input = QLineEdit()
        self._provision_path_input.setPlaceholderText("/path/on/device")
        write_layout.addRow("Device Path:", self._provision_path_input)
        write_btn_row = QHBoxLayout()
        write_btn = QPushButton("Choose File & Write…")
        write_btn.setObjectName("write_file")
        write_btn.clicked.connect(self._provision_write_file)
        write_btn_row.addWidget(write_btn)
        write_btn_row.addStretch()
        write_layout.addRow("", write_btn_row)
        layout.addWidget(write_group)

        # Danger Zone
        fs_group = QGroupBox("Danger Zone")
        fs_group.setStyleSheet("QGroupBox { border: 2px solid #cc0000; color: #cc0000; }")
        fs_layout = QVBoxLayout(fs_group)
        fs_layout.addWidget(QLabel("Reformatting the filesystem erases all stored files and certificates."))
        self._reformat_btn = QPushButton("Reformat Filesystem")
        self._reformat_btn.setStyleSheet("color: #cc0000;")
        self._reformat_btn.clicked.connect(self._provision_reformat_fs)
        fs_layout.addWidget(self._reformat_btn)
        layout.addWidget(fs_group)

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
        """Set the current device and setup the provision worker."""
        self._device = device
        self._setup_provision_worker()
        self._set_controls_enabled(True)
        self._status_label.setText("Device connected")

    def clear_device(self) -> None:
        """Clear the current device and cleanup the provision worker."""
        self._cleanup_provision_worker()
        self._device = None
        self._set_controls_enabled(False)
        self._status_label.setText("No device connected")

    # -------------------------------------------------------------------------

    def _setup_provision_worker(self) -> None:
        """Setup the provision worker thread."""
        self._cleanup_provision_worker()
        if not self._device:
            return
        self._provision_thread = QThread()
        self._provision_worker = ProvisionWorker(self._device)
        self._provision_worker.moveToThread(self._provision_thread)
        self._provision_worker.operation_completed.connect(self._on_provision_completed)
        self._provision_worker.keypair_generated.connect(self._on_keypair_generated)
        self._provision_worker.error_occurred.connect(self._on_provision_error)
        self._provision_thread.start()

    def _cleanup_provision_worker(self) -> None:
        """Cleanup the provision worker thread."""
        if self._provision_thread:
            self._provision_thread.quit()
            self._provision_thread.wait()
            self._provision_thread = None
        self._provision_worker = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable controls based on device connection."""
        for key_type in ["ed25519", "p256", "x25519"]:
            gen_btn = self.findChild(QPushButton, f"gen_{key_type}")
            if gen_btn:
                gen_btn.setEnabled(enabled)
            import_btn = self.findChild(QPushButton, f"import_cert_{key_type}")
            if import_btn:
                import_btn.setEnabled(enabled)
        import_t1_btn = self.findChild(QPushButton, "import_t1")
        if import_t1_btn:
            import_t1_btn.setEnabled(enabled)
        write_btn = self.findChild(QPushButton, "write_file")
        if write_btn:
            write_btn.setEnabled(enabled)
        self._reformat_btn.setEnabled(enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Set busy state with progress indicator."""
        self._status_progress.setVisible(busy)
        if busy:
            self._status_label.setText(message)
        else:
            self._status_label.setText("Ready")

    # -------------------------------------------------------------------------
    # Provision actions

    def _provision_generate_key(self, key_type: str) -> None:
        """Generate an attestation keypair on the device."""
        if self._provision_worker:
            self._set_busy(True, f"Generating {key_type} key...")
            self._provision_worker.generate_key(key_type)

    def _provision_import_cert(self, key_type: str) -> None:
        """Import an attestation certificate for the given key type."""
        if not self._provision_worker:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Import {key_type} Certificate", "",
            "Certificates (*.der *.pem *.crt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            if b"-----BEGIN CERTIFICATE-----" in data:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives.serialization import Encoding
                cert = x509.load_pem_x509_certificate(data, default_backend())
                data = cert.public_bytes(Encoding.DER)
            self._set_busy(True, f"Storing {key_type} certificate...")
            self._provision_worker.store_certificate(key_type, data)
        except Exception as e:
            self._set_busy(False)
            QMessageBox.critical(self, "Error", f"Failed to read certificate: {e}")

    def _provision_import_t1_pubkey(self) -> None:
        """Import the T1 intermediate public key."""
        if not self._provision_worker:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import T1 Public Key (32 bytes)", "", "All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._set_busy(True, "Storing T1 public key...")
            self._provision_worker.store_t1_pubkey(data)
        except Exception as e:
            self._set_busy(False)
            QMessageBox.critical(self, "Error", f"Failed to read file: {e}")

    def _provision_write_file(self) -> None:
        """Write a file to the device filesystem."""
        if not self._provision_worker:
            return
        device_path = self._provision_path_input.text().strip()
        if not device_path:
            QMessageBox.warning(self, "No Path", "Enter a device path first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select File to Write", "", "All Files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._set_busy(True, f"Writing file to {device_path}...")
            self._provision_worker.write_file(device_path, data)
        except Exception as e:
            self._set_busy(False)
            QMessageBox.critical(self, "Error", f"Failed to read file: {e}")

    def _provision_reformat_fs(self) -> None:
        """Reformat the device filesystem."""
        if not self._provision_worker:
            return
        reply = QMessageBox.warning(
            self,
            "Reformat Filesystem",
            "This will permanently erase all files and certificates on the device.\n\n"
            "Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            reply2 = QMessageBox.critical(
                self,
                "Final Confirmation",
                "FINAL WARNING: All filesystem data will be erased.\n\nProceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply2 == QMessageBox.Yes:
                self._set_busy(True, "Reformatting filesystem...")
                self._provision_worker.reformat_filesystem()

    # -------------------------------------------------------------------------
    # Worker slots

    def _on_keypair_generated(self, key_type: str, pubkey_bytes: bytes) -> None:
        """Handle keypair generated signal from worker."""
        self._set_busy(False)
        field = self.findChild(QLineEdit, f"pubkey_{key_type}")
        if field:
            field.setText(pubkey_bytes.hex())
        self._status_label.setText(f"{key_type} key generated successfully")

    def _on_provision_completed(self, success: bool, message: str) -> None:
        """Handle provision operation completed signal from worker."""
        self._set_busy(False)
        if success:
            self._status_label.setText(message)
            QMessageBox.information(self, "Success", message)
        else:
            self._status_label.setText(f"Failed: {message}")
            QMessageBox.critical(self, "Error", message)

    def _on_provision_error(self, error: str) -> None:
        """Handle provision error signal from worker."""
        self._set_busy(False)
        self._status_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Provision Error", error)
