"""PIV tab for SoloKeys GUI."""

from typing import Optional, List

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
    QTabWidget,
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

from solo_gui.models.device import SoloDevice
from solo_gui.workers.piv_worker import (
    PivWorker,
    PivKey,
    PivCertificate,
    PivSlot,
    PivKeyType,
    PCSC_AVAILABLE,
    DEFAULT_MANAGEMENT_KEY,
)


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
    """Dialog for generating a new PIV key."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate PIV Key")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Slot selection
        self._slot_combo = QComboBox()
        self._slot_combo.addItem("Authentication (9A)", PivSlot.AUTHENTICATION)
        self._slot_combo.addItem("Digital Signature (9C)", PivSlot.SIGNATURE)
        self._slot_combo.addItem("Key Management (9D)", PivSlot.KEY_MANAGEMENT)
        self._slot_combo.addItem("Card Authentication (9E)", PivSlot.CARD_AUTH)
        form_layout.addRow("Slot:", self._slot_combo)

        # Algorithm selection
        self._algo_combo = QComboBox()
        self._algo_combo.addItem("ECC P-256 (Recommended)", PivKeyType.ECC_P256)
        self._algo_combo.addItem("ECC P-384", PivKeyType.ECC_P384)
        # Note: RSA requires 'rsa' feature in piv-authenticator which is not enabled
        # self._algo_combo.addItem("RSA 2048", PivKeyType.RSA_2048)
        form_layout.addRow("Algorithm:", self._algo_combo)

        # PIN input
        self._pin_input = QLineEdit()
        self._pin_input.setEchoMode(QLineEdit.Password)
        form_layout.addRow("PIN:", self._pin_input)

        # Management key input
        self._mgmt_key_input = QLineEdit()
        self._mgmt_key_input.setText(DEFAULT_MANAGEMENT_KEY)
        self._mgmt_key_input.setPlaceholderText("24-byte hex management key")
        form_layout.addRow("Management Key:", self._mgmt_key_input)

        layout.addLayout(form_layout)

        layout.addWidget(
            QLabel(
                "Warning: This will overwrite any existing key in the selected slot."
            )
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_slot(self) -> PivSlot:
        return self._slot_combo.currentData()

    def get_key_type(self) -> PivKeyType:
        return self._algo_combo.currentData()

    def get_pin(self) -> str:
        return self._pin_input.text()

    def get_mgmt_key(self) -> str:
        return self._mgmt_key_input.text()


class PivTab(QWidget):
    """PIV tab for managing SSH/GPG keys and certificates."""

    piv_availability = Signal(bool)  # emitted once per device connect

    def __init__(self):
        super().__init__()
        self._device: Optional[SoloDevice] = None
        self._worker: Optional[PivWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._keys: List[PivKey] = []
        self._certificates: List[PivCertificate] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        layout = QVBoxLayout(self)

        # PCSC status warning
        if not PCSC_AVAILABLE:
            warning_label = QLabel(
                "⚠️ PCSC not available. PIV functionality requires:\n"
                "  • sudo apt install pcscd pcsc-tools\n"
                "  • pip install pyscard"
            )
            warning_label.setStyleSheet(
                "background-color: #fff3cd; padding: 10px; border-radius: 5px;"
            )
            layout.addWidget(warning_label)

        # Create sub-tabs for PIV functionality
        self._tab_widget = QTabWidget()

        # Keys tab
        self._keys_tab = self._create_keys_tab()
        self._tab_widget.addTab(self._keys_tab, "Keys")

        # Certificates tab
        self._certs_tab = self._create_certificates_tab()
        self._tab_widget.addTab(self._certs_tab, "Certificates")

        # PIN Management tab
        self._pin_tab = self._create_pin_tab()
        self._tab_widget.addTab(self._pin_tab, "PIN Management")

        layout.addWidget(self._tab_widget)

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

        layout.addLayout(status_layout)

        # Initially disable controls
        self._set_controls_enabled(False)

    def _create_keys_tab(self) -> QWidget:
        """Create the keys management tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Keys table
        self._keys_table = QTableWidget()
        self._keys_table.setColumnCount(3)
        self._keys_table.setHorizontalHeaderLabels(["Slot", "Algorithm", "Certificate"])

        self._keys_table.setEditTriggers(QTableWidget.NoEditTriggers)

        header = self._keys_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        layout.addWidget(self._keys_table)

        # Key actions
        actions_layout = QHBoxLayout()

        self._refresh_keys_button = QPushButton("Refresh Keys")
        self._refresh_keys_button.clicked.connect(self._refresh_keys)
        actions_layout.addWidget(self._refresh_keys_button)

        self._generate_key_button = QPushButton("Generate New Key")
        self._generate_key_button.clicked.connect(self._generate_key)
        actions_layout.addWidget(self._generate_key_button)

        self._delete_key_button = QPushButton("Delete Key")
        self._delete_key_button.clicked.connect(self._delete_key)
        self._delete_key_button.setEnabled(False)
        actions_layout.addWidget(self._delete_key_button)

        self._diagnose_button = QPushButton("Diagnose PCSC")
        self._diagnose_button.setToolTip(
            "Probe PCSC readers to see which applets (PIV, Provision, …) respond"
        )
        self._diagnose_button.clicked.connect(self._run_diagnose)
        actions_layout.addWidget(self._diagnose_button)

        actions_layout.addStretch()
        layout.addLayout(actions_layout)

        # Connect selection signal
        self._keys_table.itemSelectionChanged.connect(self._on_key_selection_changed)

        return tab

    def _create_certificates_tab(self) -> QWidget:
        """Create the certificates management tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Certificates table
        self._certs_table = QTableWidget()
        self._certs_table.setColumnCount(4)
        self._certs_table.setHorizontalHeaderLabels(
            ["Slot", "Subject", "Issuer", "Expires"]
        )

        self._certs_table.setEditTriggers(QTableWidget.NoEditTriggers)

        header = self._certs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        layout.addWidget(self._certs_table)

        # Certificate actions
        actions_layout = QHBoxLayout()

        self._refresh_certs_button = QPushButton("Refresh Certificates")
        self._refresh_certs_button.clicked.connect(self._refresh_certificates)
        actions_layout.addWidget(self._refresh_certs_button)

        self._import_cert_button = QPushButton("Import Certificate")
        self._import_cert_button.clicked.connect(self._import_certificate)
        actions_layout.addWidget(self._import_cert_button)

        self._export_cert_button = QPushButton("Export Certificate")
        self._export_cert_button.clicked.connect(self._export_certificate)
        self._export_cert_button.setEnabled(False)
        actions_layout.addWidget(self._export_cert_button)

        self._delete_cert_button = QPushButton("Delete Certificate")
        self._delete_cert_button.clicked.connect(self._delete_certificate)
        self._delete_cert_button.setEnabled(False)
        actions_layout.addWidget(self._delete_cert_button)

        actions_layout.addStretch()
        layout.addLayout(actions_layout)

        # Connect selection signal
        self._certs_table.itemSelectionChanged.connect(self._on_cert_selection_changed)

        return tab

    def _create_pin_tab(self) -> QWidget:
        """Create the PIN management tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # PIN status
        status_group = QGroupBox("PIN Status")
        status_layout = QVBoxLayout(status_group)

        # Default PIN info
        default_pin_label = QLabel("Default PIN: 123456 | Default PUK: 12345678")
        default_pin_label.setStyleSheet("color: gray; font-size: 10px;")
        status_layout.addWidget(default_pin_label)

        pin_info_layout = QHBoxLayout()
        pin_info_layout.addWidget(QLabel("PIN:"))
        self._pin_status_label = QLabel("Unknown")
        pin_info_layout.addWidget(self._pin_status_label)
        pin_info_layout.addStretch()

        puk_info_layout = QHBoxLayout()
        puk_info_layout.addWidget(QLabel("PUK:"))
        self._puk_status_label = QLabel("Unknown")
        puk_info_layout.addWidget(self._puk_status_label)
        puk_info_layout.addStretch()

        status_layout.addLayout(pin_info_layout)
        status_layout.addLayout(puk_info_layout)

        # PIN actions
        actions_group = QGroupBox("PIN Actions")
        actions_layout = QVBoxLayout(actions_group)

        # PIN actions
        pin_actions_layout = QHBoxLayout()

        self._change_pin_button = QPushButton("Change PIN")
        self._change_pin_button.clicked.connect(self._change_pin)
        pin_actions_layout.addWidget(self._change_pin_button)

        self._unblock_pin_button = QPushButton("Unblock PIN")
        self._unblock_pin_button.clicked.connect(self._unblock_pin)
        pin_actions_layout.addWidget(self._unblock_pin_button)

        pin_actions_layout.addStretch()
        actions_layout.addLayout(pin_actions_layout)

        # PUK actions
        puk_actions_layout = QHBoxLayout()

        self._change_puk_button = QPushButton("Change PUK")
        self._change_puk_button.clicked.connect(self._change_puk)
        puk_actions_layout.addWidget(self._change_puk_button)

        puk_actions_layout.addStretch()
        actions_layout.addLayout(puk_actions_layout)

        # Danger zone: PIV reset
        danger_group = QGroupBox("Danger Zone")
        danger_group.setStyleSheet(
            "QGroupBox { border: 2px solid #cc0000; color: #cc0000; }"
        )
        danger_layout = QVBoxLayout(danger_group)
        danger_layout.addWidget(
            QLabel("Resetting PIV requires both PIN and PUK to be blocked (0 retries).")
        )
        self._reset_piv_button = QPushButton("Reset PIV Applet")
        self._reset_piv_button.setStyleSheet("color: #cc0000;")
        self._reset_piv_button.clicked.connect(self._reset_piv)
        danger_layout.addWidget(self._reset_piv_button)

        layout.addWidget(status_group)
        layout.addWidget(actions_group)
        layout.addWidget(danger_group)
        layout.addStretch()

        return tab

    def set_device(self, device: SoloDevice) -> None:
        """Set the current device. Silently probes PIV availability first."""
        self._device = device
        self._setup_worker()
        # Probe silently — _on_piv_probed() handles the result
        if self._worker:
            self._worker.probe_piv()

    def clear_device(self) -> None:
        """Clear the current device."""
        self._device = None
        self._cleanup_worker()
        self._keys = []
        self._certificates = []
        self._keys_table.setRowCount(0)
        self._certs_table.setRowCount(0)
        self._pin_status_label.setText("Unknown")
        self._puk_status_label.setText("Unknown")
        self._status_label.setText("No device connected")
        self._set_controls_enabled(False)

    def _setup_worker(self) -> None:
        """Setup the PIV worker thread."""
        if not self._device:
            return

        # Cleanup existing worker
        self._cleanup_worker()

        # Create new worker thread
        self._worker_thread = QThread()
        self._worker = PivWorker(self._device)
        self._worker.moveToThread(self._worker_thread)

        # Connect signals
        self._worker.piv_probed.connect(self._on_piv_probed)
        self._worker.keys_loaded.connect(self._on_keys_loaded)
        self._worker.certificates_loaded.connect(self._on_certificates_loaded)
        self._worker.key_generated.connect(self._on_key_generated)
        self._worker.key_deleted.connect(self._on_key_deleted)
        self._worker.certificate_imported.connect(self._on_certificate_imported)
        self._worker.certificate_exported.connect(self._on_certificate_exported)
        self._worker.pin_changed.connect(self._on_pin_changed)
        self._worker.pin_status_updated.connect(self._on_pin_status_updated)
        self._worker.reset_completed.connect(self._on_reset_completed)
        self._worker.pcsc_status.connect(self._on_pcsc_status)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker.diagnose_result.connect(self._on_diagnose_result)

        # Start thread
        self._worker_thread.start()

    def _cleanup_worker(self) -> None:
        """Cleanup the worker thread."""
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

    def _on_piv_probed(self, available: bool) -> None:
        """Handle PIV availability probe result."""
        self.piv_availability.emit(available)
        if available:
            self._set_controls_enabled(True)
            self._refresh_keys()
            self._refresh_certificates()
            self._update_pin_status()
        else:
            self._set_controls_enabled(False)
            self._status_label.setText("PIV applet not found on this firmware")

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable controls."""
        pcsc_enabled = enabled and PCSC_AVAILABLE
        self._refresh_keys_button.setEnabled(pcsc_enabled)
        self._generate_key_button.setEnabled(pcsc_enabled)
        self._diagnose_button.setEnabled(pcsc_enabled)
        self._refresh_certs_button.setEnabled(pcsc_enabled)
        self._import_cert_button.setEnabled(pcsc_enabled)
        self._change_pin_button.setEnabled(pcsc_enabled)
        self._unblock_pin_button.setEnabled(pcsc_enabled)
        self._change_puk_button.setEnabled(pcsc_enabled)
        self._reset_piv_button.setEnabled(pcsc_enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Set busy state with progress indicator."""
        self._status_progress.setVisible(busy)
        if busy:
            self._status_label.setText(message)
        else:
            self._status_label.setText("Ready")

    def _on_key_selection_changed(self) -> None:
        """Handle key selection change."""
        has_selection = bool(self._keys_table.selectedItems())
        self._delete_key_button.setEnabled(has_selection and PCSC_AVAILABLE)

    def _on_cert_selection_changed(self) -> None:
        """Handle certificate selection change."""
        has_selection = bool(self._certs_table.selectedItems())
        self._delete_cert_button.setEnabled(has_selection and PCSC_AVAILABLE)
        self._export_cert_button.setEnabled(has_selection and PCSC_AVAILABLE)

    def _slot_name(self, slot: PivSlot) -> str:
        """Get human-readable slot name."""
        names = {
            PivSlot.AUTHENTICATION: "Authentication (9A)",
            PivSlot.SIGNATURE: "Digital Signature (9C)",
            PivSlot.KEY_MANAGEMENT: "Key Management (9D)",
            PivSlot.CARD_AUTH: "Card Auth (9E)",
        }
        return names.get(slot, str(slot))

    def _refresh_keys(self) -> None:
        """Refresh the keys list."""
        if not self._worker:
            return
        self._set_busy(True, "Loading keys...")
        self._worker.load_keys()

    def _on_keys_loaded(self, keys: List[PivKey]) -> None:
        """Handle keys loaded from worker."""
        self._set_busy(False)
        self._keys = keys
        self._keys_table.setRowCount(0)

        for i, key in enumerate(keys):
            self._keys_table.insertRow(i)
            self._keys_table.setItem(i, 0, QTableWidgetItem(self._slot_name(key.slot)))
            self._keys_table.setItem(i, 1, QTableWidgetItem(key.algorithm))
            self._keys_table.setItem(
                i, 2, QTableWidgetItem("Yes" if key.has_certificate else "No")
            )

    def _generate_key(self) -> None:
        """Generate a new PIV key."""
        if not self._worker:
            return

        dialog = GenerateKeyDialog(self)
        if dialog.exec() == QDialog.Accepted:
            slot = dialog.get_slot()
            key_type = dialog.get_key_type()
            pin = dialog.get_pin()
            mgmt_key = dialog.get_mgmt_key()

            self._set_busy(True, f"Generating key in {self._slot_name(slot)}...")
            self._worker.generate_key(slot, key_type, pin if pin else None, mgmt_key)

    def _on_key_generated(self, success: bool, error: str, pubkey_der: bytes, slot: PivSlot = None, pin: str = None, mgmt_key: str = None) -> None:
        """Handle key generation result."""
        self._set_busy(False)
        if success:
            # Automatically create and import a self-signed certificate
            if pubkey_der and slot and mgmt_key:
                try:
                    self._set_busy(True, "Creating self-signed certificate...")
                    from cryptography import x509
                    from cryptography.x509.oid import NameOID
                    from cryptography.hazmat.primitives import hashes, serialization
                    from cryptography.hazmat.primitives.asymmetric import rsa, ec
                    from cryptography.hazmat.backends import default_backend
                    import datetime
                    
                    # Load the public key
                    pub_key = serialization.load_der_public_key(pubkey_der, backend=default_backend())
                    
                    # Determine algorithm from key type
                    if isinstance(pub_key, rsa.RSAPublicKey):
                        # For RSA, we need the private key to sign - but we don't have it
                        # Instead, we'll create a certificate request and import it
                        # Actually, we can't create a self-signed cert without the private key
                        # So we'll just import a placeholder certificate
                        QMessageBox.information(
                            self, 
                            "Key Generated", 
                            "Key generated successfully.\n\n"
                            "Note: To complete the setup, you should import a certificate "
                            "for this key or use OpenSSL to create a CSR and sign it."
                        )
                    elif isinstance(pub_key, ec.EllipticCurvePublicKey):
                        # Same issue for ECC
                        QMessageBox.information(
                            self,
                            "Key Generated",
                            "Key generated successfully.\n\n"
                            "Note: To complete the setup, you should import a certificate "
                            "for this key or use OpenSSL to create a CSR and sign it."
                        )
                    else:
                        QMessageBox.information(self, "Success", "Key generated successfully")
                    
                    self._refresh_keys()
                    self._show_pubkey_dialog(pubkey_der)
                    
                except Exception as e:
                    print(f"[PIV] Error creating certificate: {e}")
                    self._refresh_keys()
                    if pubkey_der:
                        self._show_pubkey_dialog(pubkey_der)
                    else:
                        QMessageBox.information(self, "Success", "Key generated successfully")
            else:
                self._refresh_keys()
                if pubkey_der:
                    self._show_pubkey_dialog(pubkey_der)
                else:
                    QMessageBox.information(self, "Success", "Key generated successfully")
        else:
            QMessageBox.critical(self, "Error", f"Failed to generate key: {error}")

    def _show_pubkey_dialog(self, pubkey_der: bytes) -> None:
        """Show dialog with public key in PEM format."""
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
        layout.addWidget(QLabel("Key generated successfully. Public key:"))
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

    def _delete_key(self) -> None:
        """Delete the selected key."""
        current_row = self._keys_table.currentRow()
        if current_row < 0 or current_row >= len(self._keys):
            return

        key = self._keys[current_row]
        slot_name = self._slot_name(key.slot)

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the key in {slot_name}?\n\n"
            "This will also delete any associated certificate.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes and self._worker:
            # Get PIN for deletion
            dialog = PivPinDialog(self, "Enter PIN", show_current=True, show_new=False)
            if dialog.exec() == QDialog.Accepted:
                self._set_busy(True, "Deleting key...")
                self._worker.delete_certificate(key.slot, dialog.get_current())

    def _on_key_deleted(self, success: bool, error: str) -> None:
        """Handle key deletion result."""
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", "Key deleted successfully")
            self._refresh_keys()
            self._refresh_certificates()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete key: {error}")

    def _refresh_certificates(self) -> None:
        """Refresh the certificates list."""
        if not self._worker:
            return
        self._set_busy(True, "Loading certificates...")
        self._worker.load_certificates()

    def _on_certificates_loaded(self, certificates: List[PivCertificate]) -> None:
        """Handle certificates loaded from worker."""
        self._set_busy(False)
        self._certificates = certificates
        self._certs_table.setRowCount(0)

        for i, cert in enumerate(certificates):
            self._certs_table.insertRow(i)
            self._certs_table.setItem(i, 0, QTableWidgetItem(self._slot_name(cert.slot)))
            self._certs_table.setItem(i, 1, QTableWidgetItem(cert.subject))
            self._certs_table.setItem(i, 2, QTableWidgetItem(cert.issuer))
            self._certs_table.setItem(i, 3, QTableWidgetItem(cert.not_after))

    def _import_certificate(self) -> None:
        """Import a certificate."""
        if not self._worker:
            return

        # Select certificate file
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

            # Check if PEM format and convert to DER if needed
            if b"-----BEGIN CERTIFICATE-----" in cert_data:
                try:
                    from cryptography import x509
                    from cryptography.hazmat.backends import default_backend

                    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
                    cert_data = cert.public_bytes(
                        encoding=__import__(
                            "cryptography.hazmat.primitives.serialization",
                            fromlist=["Encoding"],
                        ).Encoding.DER
                    )
                except ImportError:
                    QMessageBox.warning(
                        self,
                        "PEM Not Supported",
                        "PEM format requires cryptography library. "
                        "Please use DER format.",
                    )
                    return

            # Select slot + credentials
            slot_dialog = QDialog(self)
            slot_dialog.setWindowTitle("Import Certificate")
            slot_layout = QVBoxLayout(slot_dialog)
            form = QFormLayout()

            slot_combo = QComboBox()
            slot_combo.addItem("Authentication (9A)", PivSlot.AUTHENTICATION)
            slot_combo.addItem("Digital Signature (9C)", PivSlot.SIGNATURE)
            slot_combo.addItem("Key Management (9D)", PivSlot.KEY_MANAGEMENT)
            slot_combo.addItem("Card Authentication (9E)", PivSlot.CARD_AUTH)
            form.addRow("Slot:", slot_combo)

            pin_input = QLineEdit()
            pin_input.setEchoMode(QLineEdit.Password)
            form.addRow("PIN (optional):", pin_input)

            mgmt_input = QLineEdit()
            mgmt_input.setText(DEFAULT_MANAGEMENT_KEY)
            form.addRow("Management Key:", mgmt_input)

            slot_layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(slot_dialog.accept)
            buttons.rejected.connect(slot_dialog.reject)
            slot_layout.addWidget(buttons)

            if slot_dialog.exec() == QDialog.Accepted:
                slot = slot_combo.currentData()
                pin = pin_input.text()
                mgmt_key = mgmt_input.text()
                self._set_busy(True, "Importing certificate...")
                self._worker.import_certificate(
                    slot, cert_data, pin if pin else None, mgmt_key
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read certificate: {e}")

    def _on_certificate_imported(self, success: bool, error: str) -> None:
        """Handle certificate import result."""
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Success", "Certificate imported successfully")
            self._refresh_certificates()
        else:
            QMessageBox.critical(self, "Error", f"Failed to import certificate: {error}")

    def _export_certificate(self) -> None:
        """Export the selected certificate."""
        current_row = self._certs_table.currentRow()
        if current_row < 0 or current_row >= len(self._certificates):
            return

        cert = self._certificates[current_row]
        self._set_busy(True, "Exporting certificate...")
        self._worker.export_certificate(cert.slot)

    def _on_certificate_exported(
        self, success: bool, error_or_path: str, cert_data: bytes
    ) -> None:
        """Handle certificate export result."""
        self._set_busy(False)
        if success and cert_data:
            # Ask user for save location
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
                            from cryptography.hazmat.primitives.serialization import (
                                Encoding,
                            )

                            cert = x509.load_der_x509_certificate(
                                cert_data, default_backend()
                            )
                            cert_data = cert.public_bytes(Encoding.PEM)
                        except ImportError:
                            QMessageBox.warning(
                                self,
                                "PEM Not Supported",
                                "PEM format requires cryptography library. "
                                "Saving as DER.",
                            )
                            file_path = file_path.replace(".pem", ".der")

                    with open(file_path, "wb") as f:
                        f.write(cert_data)
                    QMessageBox.information(
                        self, "Success", f"Certificate saved to {file_path}"
                    )
                except Exception as e:
                    QMessageBox.critical(
                        self, "Error", f"Failed to save certificate: {e}"
                    )
        else:
            QMessageBox.critical(
                self, "Error", f"Failed to export certificate: {error_or_path}"
            )

    def _delete_certificate(self) -> None:
        """Delete the selected certificate."""
        current_row = self._certs_table.currentRow()
        if current_row < 0 or current_row >= len(self._certificates):
            return

        cert = self._certificates[current_row]
        slot_name = self._slot_name(cert.slot)

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the certificate in {slot_name}?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes and self._worker:
            dialog = PivPinDialog(self, "Enter PIN", show_current=True, show_new=False)
            if dialog.exec() == QDialog.Accepted:
                self._set_busy(True, "Deleting certificate...")
                self._worker.delete_certificate(cert.slot, dialog.get_current())

    def _update_pin_status(self) -> None:
        """Update PIN status display."""
        if not self._worker:
            return
        self._worker.get_pin_status()

    def _on_pin_status_updated(self, status: dict) -> None:
        """Handle PIN status update."""
        if not status.get("pcsc_available", True):
            self._pin_status_label.setText("PCSC not available")
            self._puk_status_label.setText("PCSC not available")
            return

        if not status.get("connected", True):
            self._pin_status_label.setText("Not connected")
            self._puk_status_label.setText("Not connected")
            return

        pin_retries = status.get("pin_retries")
        puk_retries = status.get("puk_retries")

        if pin_retries is not None:
            self._pin_status_label.setText(f"{pin_retries} retries remaining")
        else:
            self._pin_status_label.setText("Status unknown")

        if puk_retries is not None:
            self._puk_status_label.setText(f"{puk_retries} retries remaining")
        else:
            self._puk_status_label.setText("Status unknown")

    def _change_pin(self) -> None:
        """Change the PIV PIN."""
        if not self._worker:
            return

        dialog = PivPinDialog(
            self,
            "Change PIN",
            show_current=True,
            show_new=True,
            current_label="Current PIN:",
            new_label="New PIN:",
        )

        if dialog.exec() == QDialog.Accepted:
            current_pin = dialog.get_current()
            new_pin = dialog.get_new()
            if current_pin and new_pin:
                self._set_busy(True, "Changing PIN...")
                self._worker.change_pin(current_pin, new_pin)

    def _unblock_pin(self) -> None:
        """Unblock the PIV PIN."""
        if not self._worker:
            return

        dialog = PivPinDialog(
            self,
            "Unblock PIN",
            show_current=True,
            show_new=True,
            current_label="PUK:",
            new_label="New PIN:",
        )

        if dialog.exec() == QDialog.Accepted:
            puk = dialog.get_current()
            new_pin = dialog.get_new()
            if puk and new_pin:
                self._set_busy(True, "Unblocking PIN...")
                self._worker.unblock_pin(puk, new_pin)

    def _change_puk(self) -> None:
        """Change the PIV PUK."""
        if not self._worker:
            return

        dialog = PivPinDialog(
            self,
            "Change PUK",
            show_current=True,
            show_new=True,
            current_label="Current PUK:",
            new_label="New PUK:",
        )

        if dialog.exec() == QDialog.Accepted:
            current_puk = dialog.get_current()
            new_puk = dialog.get_new()
            if current_puk and new_puk:
                self._set_busy(True, "Changing PUK...")
                self._worker.change_puk(current_puk, new_puk)

    def _reset_piv(self) -> None:
        """Reset the PIV applet to factory defaults."""
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

    def _on_reset_completed(self, success: bool, message: str) -> None:
        """Handle PIV reset result."""
        self._set_busy(False)
        if success:
            QMessageBox.information(self, "Reset Complete", message)
            self._refresh_keys()
            self._refresh_certificates()
            self._update_pin_status()
        else:
            # Provide helpful error message
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

    def _on_pin_changed(self, success: bool, message: str) -> None:
        """Handle PIN/PUK change result."""
        self._set_busy(False)
        if success:
            QMessageBox.information(
                self, "Success", message if message else "Operation completed successfully"
            )
            self._update_pin_status()
        else:
            QMessageBox.critical(self, "Error", message)

    def _on_pcsc_status(self, available: bool, message: str) -> None:
        """Handle PCSC status update."""
        if not available:
            self._status_label.setText(message)

    def _run_diagnose(self) -> None:
        """Run PCSC diagnostic."""
        if not self._worker:
            return
        self._set_busy(True, "Running PCSC diagnostic...")
        self._worker.diagnose_pcsc()

    def _on_diagnose_result(self, report: str) -> None:
        """Show PCSC diagnostic report in a dialog."""
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
        """Handle worker error."""
        self._set_busy(False)
        self._status_label.setText(f"Error: {error}")
