"""Main window for SoloKeys GUI."""

from typing import Optional, Dict

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStatusBar,
    QMessageBox,
    QStackedWidget,
    QFrame,
    QSizePolicy,
    QStyle,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon, QAction

# Try to import standard icons, fallback to text-only if not available
try:
    from PySide6.QtGui import QIcon
    HAS_ICONS = True
except ImportError:
    HAS_ICONS = False

from ..models.device import SoloDevice, format_firmware_version
from ..models.device_monitor import DeviceMonitor
from ..device_manager import DeviceManager
from .tabs.overview_tab import OverviewTab
from .tabs.fido2_tab import Fido2Tab
from .tabs.piv_tab import PivTab
from .tabs.totp_tab import TotpTab
from .tabs.admin_tab import AdminTab
from .tabs.hacker_tab import HackerTab
from .tabs.settings_tab import SettingsTab


class SidebarButton(QPushButton):
    """Custom button for sidebar navigation."""
    
    def __init__(self, text: str, icon_name: str = None, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setMinimumHeight(50)
        self.setMaximumHeight(50)
        self.setMinimumWidth(160)
        self.setMaximumWidth(160)
        
        # Set icon if available
        if HAS_ICONS and icon_name and parent:
            style = parent.style()
            icon = style.standardIcon(getattr(QStyle, icon_name, QStyle.SP_ComputerIcon))
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))
        
        # Style
        self.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 8px 12px;
                margin: 2px 4px;
                border: none;
                border-radius: 6px;
                background-color: #f5f5f5;
                color: #333;
                font-weight: normal;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:checked {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
            }
            QPushButton:disabled {
                background-color: #e0e0e0;
                color: #999;
            }
        """)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        # DeviceMonitor handles device detection/plugging
        self._device_monitor = DeviceMonitor()
        # DeviceManager (singleton) handles all device operations
        self._device_manager = DeviceManager.get_instance()
        self._current_device: Optional[SoloDevice] = None
        self._refresh_timer = QTimer()
        
        # Track sidebar buttons and their corresponding tabs
        self._sidebar_buttons: Dict[int, SidebarButton] = {}
        self._tab_indices: Dict[str, int] = {}
        
        self._setup_ui()
        self._setup_connections()
        self._setup_menu()

        # Start device monitoring
        self._device_monitor.start_monitoring()

        # Connect to DeviceManager signals
        self._device_manager.device_connected.connect(self._on_dm_connected)
        self._device_manager.device_disconnected.connect(self._on_dm_disconnected)
        self._device_manager.error_occurred.connect(self._on_dm_error)

    def _setup_ui(self) -> None:
        self.setWindowTitle("SoloKeys GUI")
        self.setMinimumSize(1000, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Device selector
        device_layout = QHBoxLayout()
        device_layout.setContentsMargins(10, 10, 10, 10)
        self._device_label = QLabel("No device connected")
        self._refresh_button = QPushButton("Refresh")
        device_layout.addWidget(self._device_label)
        device_layout.addWidget(self._refresh_button)
        device_layout.addStretch()
        main_layout.addLayout(device_layout)

        # Main content area with sidebar and stacked widget
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Sidebar
        self._sidebar = QFrame()
        self._sidebar.setFrameShape(QFrame.Shape.StyledPanel)
        self._sidebar.setMaximumWidth(180)
        self._sidebar.setMinimumWidth(180)
        self._sidebar.setStyleSheet("background-color: #fafafa; border-right: 1px solid #ddd;")
        
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(5, 10, 5, 10)
        sidebar_layout.setSpacing(4)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Create sidebar buttons
        self._overview_btn = SidebarButton("Overview", "SP_ComputerIcon", self)
        self._fido2_btn = SidebarButton("FIDO2", "SP_DialogApplyButton", self)
        self._admin_btn = SidebarButton("Admin", "SP_TitleBarMenuButton", self)
        self._settings_btn = SidebarButton("Settings", "SP_ComputerIcon", self)
        
        # Dynamic buttons (will be shown/hidden)
        self._piv_btn = SidebarButton("PIV", "SP_FileIcon", self)
        self._totp_btn = SidebarButton("TOTP", "SP_DialogApplyButton", self)
        self._hacker_btn = SidebarButton("Hacker", "SP_MessageBoxWarning", self)
        
        # Add static buttons to sidebar
        sidebar_layout.addWidget(self._overview_btn)
        sidebar_layout.addWidget(self._fido2_btn)
        
        # Container for dynamic buttons (PIV, TOTP, Hacker)
        self._dynamic_buttons_container = QWidget()
        self._dynamic_buttons_layout = QVBoxLayout(self._dynamic_buttons_container)
        self._dynamic_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._dynamic_buttons_layout.setSpacing(4)
        self._dynamic_buttons_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        sidebar_layout.addWidget(self._dynamic_buttons_container)
        
        # Add stretch to push Admin/Settings to bottom
        sidebar_layout.addStretch()
        sidebar_layout.addWidget(self._admin_btn)
        sidebar_layout.addWidget(self._settings_btn)
        
        content_layout.addWidget(self._sidebar)
        
        # Stacked widget for content
        self._stacked_widget = QStackedWidget()
        self._setup_tabs()
        content_layout.addWidget(self._stacked_widget, 1)  # Stretch factor 1
        
        main_layout.addLayout(content_layout, 1)  # Stretch factor 1

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")
        
        # Select first tab by default
        self._select_tab(0)

    def _setup_tabs(self) -> None:
        """Setup tabs and link them to sidebar buttons."""
        self._overview_tab = OverviewTab()
        self._fido2_tab = Fido2Tab()
        self._piv_tab = PivTab()
        self._totp_tab = TotpTab()
        self._admin_tab = AdminTab()
        self._hacker_tab = HackerTab()
        self._settings_tab = SettingsTab()

        # Add static tabs
        idx = self._stacked_widget.addWidget(self._overview_tab)
        self._tab_indices['overview'] = idx
        self._sidebar_buttons[idx] = self._overview_btn
        self._overview_btn.clicked.connect(lambda checked, i=idx: self._select_tab(i))
        
        idx = self._stacked_widget.addWidget(self._fido2_tab)
        self._tab_indices['fido2'] = idx
        self._sidebar_buttons[idx] = self._fido2_btn
        self._fido2_btn.clicked.connect(lambda checked, i=idx: self._select_tab(i))
        
        idx = self._stacked_widget.addWidget(self._admin_tab)
        self._tab_indices['admin'] = idx
        self._sidebar_buttons[idx] = self._admin_btn
        self._admin_btn.clicked.connect(lambda checked, i=idx: self._select_tab(i))
        
        idx = self._stacked_widget.addWidget(self._settings_tab)
        self._tab_indices['settings'] = idx
        self._sidebar_buttons[idx] = self._settings_btn
        self._settings_btn.clicked.connect(lambda checked, i=idx: self._select_tab(i))

        # Dynamic tabs (hidden by default)
        self._piv_tab_idx = -1
        self._totp_tab_idx = -1
        self._hacker_tab_idx = -1

        self._set_tabs_enabled(False)

    def _select_tab(self, index: int) -> None:
        """Select a tab by index."""
        if 0 <= index < self._stacked_widget.count():
            self._stacked_widget.setCurrentIndex(index)
            # Update button states
            for idx, btn in self._sidebar_buttons.items():
                btn.setChecked(idx == index)

    def _add_dynamic_tab(self, widget, button: SidebarButton, name: str) -> int:
        """Add a dynamic tab with its sidebar button."""
        # Find position to insert (before Admin)
        admin_idx = self._tab_indices.get('admin', self._stacked_widget.count())
        
        # Insert into stacked widget
        idx = admin_idx
        self._stacked_widget.insertWidget(idx, widget)
        
        # Update indices for tabs after insertion point
        for key, old_idx in list(self._tab_indices.items()):
            if old_idx >= admin_idx:
                self._tab_indices[key] = old_idx + 1
                if old_idx in self._sidebar_buttons:
                    self._sidebar_buttons[old_idx + 1] = self._sidebar_buttons.pop(old_idx)
        
        # Update dynamic tab indices
        if self._piv_tab_idx >= admin_idx:
            self._piv_tab_idx += 1
        if self._totp_tab_idx >= admin_idx:
            self._totp_tab_idx += 1
        if self._hacker_tab_idx >= admin_idx:
            self._hacker_tab_idx += 1
        
        self._tab_indices[name] = idx
        self._sidebar_buttons[idx] = button
        button.clicked.connect(lambda checked, i=idx: self._select_tab(i))
        
        # Add button to dynamic buttons container
        self._dynamic_buttons_layout.addWidget(button)
        button.show()
        
        return idx

    def _remove_dynamic_tab(self, name: str, button: SidebarButton, idx: int) -> None:
        """Remove a dynamic tab and its sidebar button."""
        if idx < 0:
            return
            
        # Remove widget from stacked widget
        widget = self._stacked_widget.widget(idx)
        self._stacked_widget.removeWidget(widget)
        
        # Remove button from sidebar
        button.hide()
        button.setParent(None)
        
        # Clean up connections
        button.clicked.disconnect()
        
        # Remove from tracking
        if idx in self._sidebar_buttons:
            del self._sidebar_buttons[idx]
        if name in self._tab_indices:
            del self._tab_indices[name]
        
        # Update indices for tabs after removal
        for key, old_idx in list(self._tab_indices.items()):
            if old_idx > idx:
                self._tab_indices[key] = old_idx - 1
                if old_idx in self._sidebar_buttons:
                    self._sidebar_buttons[old_idx - 1] = self._sidebar_buttons.pop(old_idx)

    def _setup_connections(self) -> None:
        self._device_monitor.device_connected.connect(self._on_device_connected)
        self._device_monitor.device_disconnected.connect(self._on_device_disconnected)
        self._device_monitor.device_error.connect(self._on_device_monitor_error)
        self._refresh_button.clicked.connect(self._refresh_devices)
        self._piv_tab.piv_availability.connect(self._on_piv_availability)
        self._totp_tab.totp_available.connect(self._on_totp_availability)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._refresh_devices)
        file_menu.addAction(refresh_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # -------------------------------------------------------------------------
    # Event handlers

    def _on_device_connected(self, device: SoloDevice) -> None:
        if self._current_device is not None:
            self._overview_tab.clear_device()
            self._fido2_tab.clear_device()
            self._piv_tab.clear_device()
            self._totp_tab.clear_device()
            self._admin_tab.clear_device()
            self._hacker_tab.clear_device()
            self._settings_tab.clear_device()

        self._current_device = device

        # Start DeviceManager with the device path
        if device.hid_device_path:
            self._device_manager.start(device.hid_device_path)

        info = device.get_info()
        product = info.serial_number or "Solo 2"
        in_bootloader = info.mode.value == "bootloader"
        suffix = " (Bootloader)" if in_bootloader else ""
        fw_str = format_firmware_version(info.firmware_version)
        self._device_label.setText(f"Device: {product}{suffix} — fw {fw_str}")

        self._set_tabs_enabled(True)

        self._overview_tab.set_device(device)
        self._fido2_tab.set_device(device)
        self._piv_tab.set_device(device)
        self._totp_tab.set_device(device)
        self._admin_tab.set_device(device)
        self._hacker_tab.set_device(device)
        self._settings_tab.set_device(device)

        # Check if device is Hacker variant and show tab accordingly
        variant = getattr(device, '_variant', None)
        print(f"[MainWindow] Device variant: {variant}")
        is_hacker = variant == "Hacker"
        self._on_hacker_availability(is_hacker)

        mode_label = "Bootloader" if in_bootloader else "Normal"
        self._status_bar.showMessage(f"Connected to {product} ({mode_label} mode)")

    def _on_device_disconnected(self, path: str) -> None:
        if self._current_device is None:
            return
        self._current_device = None
        self._device_label.setText("No device connected")
        self._set_tabs_enabled(False)
        self._status_bar.showMessage("Device disconnected")

        self._overview_tab.clear_device()
        self._fido2_tab.clear_device()
        self._piv_tab.clear_device()
        self._totp_tab.clear_device()
        self._admin_tab.clear_device()
        self._hacker_tab.clear_device()
        self._settings_tab.clear_device()

        # Hide dynamic tabs when device disconnects
        if self._piv_tab_idx != -1:
            self._remove_dynamic_tab('piv', self._piv_btn, self._piv_tab_idx)
            self._piv_tab_idx = -1
        if self._totp_tab_idx != -1:
            self._remove_dynamic_tab('totp', self._totp_btn, self._totp_tab_idx)
            self._totp_tab_idx = -1
        if self._hacker_tab_idx != -1:
            self._remove_dynamic_tab('hacker', self._hacker_btn, self._hacker_tab_idx)
            self._hacker_tab_idx = -1

        # Stop DeviceManager
        self._device_manager.stop()
        
        # Select overview tab
        self._select_tab(0)

    def _on_tab_changed(self, index: int) -> None:
        if self._current_device is None:
            return
        tab = self._stacked_widget.widget(index)
        if tab is self._fido2_tab:
            self._fido2_tab.refresh_state()

    def _refresh_devices(self) -> None:
        self._device_monitor.refresh_devices()
        if self._current_device is not None:
            self._fido2_tab.refresh_state()
        self._status_bar.showMessage("Refreshing devices...")

    def _on_device_monitor_error(self, path: str, error: str) -> None:
        QMessageBox.warning(self, "Device Error", f"Device error: {error}")
        self._status_bar.showMessage(f"Device error: {error}")

    def _on_piv_availability(self, available: bool) -> None:
        """Show or hide PIV tab based on availability."""
        print(f"[MainWindow] PIV availability: {available}, current idx: {self._piv_tab_idx}")
        if available and self._piv_tab_idx == -1:
            self._piv_tab_idx = self._add_dynamic_tab(self._piv_tab, self._piv_btn, 'piv')
            print(f"[MainWindow] Added PIV tab at index {self._piv_tab_idx}")
            # Enable the button if device is connected
            self._piv_btn.setEnabled(self._current_device is not None)
        elif not available and self._piv_tab_idx != -1:
            self._remove_dynamic_tab('piv', self._piv_btn, self._piv_tab_idx)
            self._piv_tab_idx = -1
            print("[MainWindow] Removed PIV tab")

    def _on_totp_availability(self, available: bool) -> None:
        """Show or hide TOTP tab based on availability."""
        print(f"[MainWindow] TOTP availability: {available}, current idx: {self._totp_tab_idx}")
        if available and self._totp_tab_idx == -1:
            self._totp_tab_idx = self._add_dynamic_tab(self._totp_tab, self._totp_btn, 'totp')
            print(f"[MainWindow] Added TOTP tab at index {self._totp_tab_idx}")
            # Enable the button if device is connected
            self._totp_btn.setEnabled(self._current_device is not None)
        elif not available and self._totp_tab_idx != -1:
            self._remove_dynamic_tab('totp', self._totp_btn, self._totp_tab_idx)
            self._totp_tab_idx = -1
            print("[MainWindow] Removed TOTP tab")

    def _on_hacker_availability(self, available: bool) -> None:
        """Show or hide Hacker tab based on availability."""
        print(f"[MainWindow] Hacker availability: {available}, current idx: {self._hacker_tab_idx}")
        if available and self._hacker_tab_idx == -1:
            self._hacker_tab_idx = self._add_dynamic_tab(self._hacker_tab, self._hacker_btn, 'hacker')
            print(f"[MainWindow] Added Hacker tab at index {self._hacker_tab_idx}")
            # Enable the button if device is connected
            self._hacker_btn.setEnabled(self._current_device is not None)
        elif not available and self._hacker_tab_idx != -1:
            self._remove_dynamic_tab('hacker', self._hacker_btn, self._hacker_tab_idx)
            self._hacker_tab_idx = -1
            print("[MainWindow] Removed Hacker tab")

    def _set_tabs_enabled(self, enabled: bool) -> None:
        """Enable/disable all sidebar buttons."""
        for btn in self._sidebar_buttons.values():
            btn.setEnabled(enabled)
        # Dynamic buttons
        self._piv_btn.setEnabled(enabled and self._piv_tab_idx != -1)
        self._totp_btn.setEnabled(enabled and self._totp_tab_idx != -1)
        self._hacker_btn.setEnabled(enabled and self._hacker_tab_idx != -1)

    # DeviceManager signal handlers
    def _on_dm_connected(self, device_path: str) -> None:
        """Handle DeviceManager connected signal."""
        pass  # Device connection is handled by _on_device_connected

    def _on_dm_disconnected(self) -> None:
        """Handle DeviceManager disconnected signal."""
        pass  # Device disconnection is handled by _on_device_disconnected

    def _on_dm_error(self, operation_id: str, error: str) -> None:
        """Handle DeviceManager error signal."""
        self._status_bar.showMessage(f"Device error: {error}")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About SoloKeys GUI",
            "SoloKeys GUI v0.1.0\n\n"
            "Platform-independent GUI for managing SoloKeys Solo 2 FIDO2 tokens.\n\n"
            "© 2024 SoloKeys GUI Team",
        )

    def closeEvent(self, event) -> None:
        self._device_manager.stop()
        self._device_monitor.stop_monitoring()
        event.accept()
