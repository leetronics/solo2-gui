"""Main window for SoloKeys GUI."""

import os
import sys
import configparser
from pathlib import Path
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
    QApplication,
)
from PySide6.QtCore import Qt, QTimer, QSize, QThread, QMetaObject, QEvent
from PySide6.QtGui import QIcon, QAction
from solo_gui import __version__

# Try to import QtAwesome for better icons, fallback to QStyle if not available
try:
    import qtawesome as qta
    HAS_QTAWESOME = True
except ImportError:
    HAS_QTAWESOME = False
    from PySide6.QtWidgets import QStyle

from ..models.device import SoloDevice, format_firmware_version
from ..workers.update_worker import UpdateCheckWorker
from ..models.device_monitor import DeviceMonitor
from ..device_manager import DeviceManager
from .tabs.overview_tab import OverviewTab
from .tabs.fido2_tab import Fido2Tab
from .tabs.piv_tab import PivTab
from .tabs.vault_tab import VaultTab
from .tabs.admin_tab import AdminTab
from .tabs.settings_tab import SettingsTab
from .tabs.gpg_tab import GpgTab

_ICON_RESOURCES_DIR = (
    __file__
    and __import__('pathlib').Path(__file__).parent.parent / "resources"
)


def _is_dark_mode() -> bool:
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False

    color_scheme = QApplication.styleHints().colorScheme()
    if color_scheme == Qt.ColorScheme.Dark:
        return True


def _get_sidebar_colors() -> dict:
    """Get color scheme for sidebar based on dark/light mode."""
    if _is_dark_mode():
        return {
            'bg': '#2d2d2d',
            'border': '#444',
            'btn_bg': '#3d3d3d',
            'btn_hover': '#4d4d4d',
            'btn_text': '#e0e0e0',
            'btn_checked_bg': '#2196F3',
            'btn_checked_text': 'white',
            'btn_disabled_bg': '#333',
            'btn_disabled_text': '#777',
        }
    else:
        return {
            'bg': '#fafafa',
            'border': '#ddd',
            'btn_bg': '#f5f5f5',
            'btn_hover': '#e0e0e0',
            'btn_text': '#333',
            'btn_checked_bg': '#2196F3',
            'btn_checked_text': 'white',
            'btn_disabled_bg': '#e0e0e0',
            'btn_disabled_text': '#999',
        }
    if color_scheme == Qt.ColorScheme.Light:
        return False

    gtk_theme = os.environ.get("GTK_THEME", "").lower()
    if "dark" in gtk_theme:
        return True

    gtk_settings = Path.home() / ".config" / "gtk-3.0" / "settings.ini"
    if gtk_settings.exists():
        parser = configparser.ConfigParser()
        parser.read(gtk_settings)
        if parser.has_option("Settings", "gtk-theme-name"):
            theme = parser.get("Settings", "gtk-theme-name").lower()
            if "dark" in theme:
                return True

    try:
        import subprocess
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            theme = result.stdout.strip().lower()
            if "dark" in theme:
                return True
    except Exception:
        pass

    try:
        import subprocess
        result = subprocess.run(
            ["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            theme = result.stdout.strip().lower()
            if "dark" in theme:
                return True
    except Exception:
        pass

    try:
        import subprocess
        result = subprocess.run(
            ["xfconf-query", "-c", "xfwm4", "-p", "/general/theme"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            theme = result.stdout.strip().lower()
            if "dark" in theme:
                return True
    except Exception:
        pass

    try:
        import subprocess
        result = subprocess.run(
            ["xfconf-query", "-c", "xfce4-panel", "-p", "/panels/panel-1/background-style"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and "0" not in result.stdout:
            return True
    except Exception:
        pass

    return False


def _get_icon_path() -> "Path":
    if sys.platform == "win32":
        if _is_dark_mode():
            return _ICON_RESOURCES_DIR / "icon-dark.ico"
        return _ICON_RESOURCES_DIR / "icon-light.ico"
    if _is_dark_mode():
        return _ICON_RESOURCES_DIR / "logo-dark.png"
    else:
        return _ICON_RESOURCES_DIR / "logo-light.png"


# Icon mapping for sidebar buttons using Material Design Icons
SIDEBAR_ICONS = {
    'overview': 'fa5s.home',
    'fido2': 'fa5s.key',
    'vault': 'fa5s.archive',
    'piv': 'fa5s.id-card',
    'gpg': 'fa5s.lock',
    'admin': 'fa5s.shield-alt',
    'settings': 'fa5s.cog',
}


class SidebarButton(QPushButton):
    """Custom button for sidebar navigation."""
    
    def __init__(self, text: str, icon_key: str = None, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setMinimumHeight(50)
        self.setMaximumHeight(50)
        self.setMinimumWidth(160)
        self.setMaximumWidth(160)
        
        # Set icon using QtAwesome (Material Design Icons) when available
        if HAS_QTAWESOME and icon_key:
            try:
                icon = qta.icon(SIDEBAR_ICONS.get(icon_key, 'fa5s.circle'), 
                               color='black',
                               color_active='white')
                self.setIcon(icon)
                self.setIconSize(QSize(22, 22))
            except Exception:
                # Fallback to no icon if qtawesome fails
                pass
        elif parent:
            # Fallback to basic QStyle icons
            style = parent.style()
            icon = style.standardIcon(QStyle.SP_ComputerIcon)
            self.setIcon(icon)
            self.setIconSize(QSize(22, 22))
        
        # Style with dark mode support
        colors = _get_sidebar_colors()
        self.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 8px 12px;
                margin: 2px 4px;
                border: none;
                border-radius: 6px;
                background-color: {colors['btn_bg']};
                color: {colors['btn_text']};
                font-weight: normal;
            }}
            QPushButton:hover {{
                background-color: {colors['btn_hover']};
            }}
            QPushButton:checked {{
                background-color: {colors['btn_checked_bg']};
                color: {colors['btn_checked_text']};
                font-weight: bold;
            }}
            QPushButton:disabled {{
                background-color: {colors['btn_disabled_bg']};
                color: {colors['btn_disabled_text']};
            }}
        """)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, browser_server=None):
        super().__init__()
        self._browser_server = browser_server
        # DeviceMonitor handles device detection/plugging
        self._device_monitor = DeviceMonitor()
        # DeviceManager (singleton) handles all device operations
        self._device_manager = DeviceManager.get_instance()
        self._current_device: Optional[SoloDevice] = None
        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._run_expected_reconnect_scan)
        self._reconnect_scan_attempts_remaining = 0
        self._shutdown_done = False

        # widget → sidebar button mapping (populated in _setup_tabs)
        self._tab_buttons: Dict[object, SidebarButton] = {}
        self._last_active_tab: Optional[QWidget] = None
        self._pending_restore_tab: Optional[QWidget] = None

        self._setup_ui()
        self._setup_connections()
        self._setup_menu()

        # Select first tab now that _setup_tabs has run
        self._select_tab(self._overview_tab)

        # Start device monitoring
        self._device_monitor.start_monitoring()

        # Connect to DeviceManager signals
        self._device_manager.device_connected.connect(self._on_dm_connected)
        self._device_manager.device_disconnected.connect(self._on_dm_disconnected)
        self._device_manager.error_occurred.connect(self._on_dm_error)

        # Update checker
        self._manual_update_check = False
        self._update_thread = QThread(self)
        self._update_worker = UpdateCheckWorker()
        self._update_worker.moveToThread(self._update_thread)
        self._update_worker.update_checked.connect(self._on_update_checked)
        self._update_thread.start()
        QTimer.singleShot(3000, self._check_for_updates)

        # Clean up properly when the application actually quits.
        QApplication.instance().aboutToQuit.connect(self._shutdown_app)

    def event(self, event) -> bool:
        return super().event(event)

    def closeEvent(self, event) -> None:
        self._shutdown_app()
        super().closeEvent(event)

    def _setup_ui(self) -> None:
        self.setWindowTitle("SoloKeys GUI")
        self.setMinimumSize(900, 600)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Device selector
        device_layout = QHBoxLayout()
        device_layout.setContentsMargins(10, 10, 10, 10)
        self._device_label = QLabel("No device connected")
        self._variant_help_lbl = QLabel()
        if HAS_QTAWESOME:
            try:
                _help_icon = qta.icon('fa5s.question-circle', color='gray')
                self._variant_help_lbl.setPixmap(_help_icon.pixmap(QSize(14, 14)))
            except Exception:
                self._variant_help_lbl.setText("?")
        else:
            self._variant_help_lbl.setText("?")
        self._variant_help_lbl.setToolTip(
            "The locked/unlocked status is read from the device firmware.\n"
            "For a definitive hardware-level check use\n"
            "'Check Variant' in the Overview tab."
        )
        self._variant_help_lbl.setVisible(False)
        self._refresh_button = QPushButton("Refresh")
        device_layout.addWidget(self._device_label)
        device_layout.addSpacing(4)
        device_layout.addWidget(self._variant_help_lbl)
        device_layout.addWidget(self._refresh_button)
        device_layout.addStretch()
        main_layout.addLayout(device_layout)

        # Update banner (hidden by default)
        self._update_banner = QWidget()
        self._update_banner.setVisible(False)
        self._update_banner.setStyleSheet(
            "background:#1565C0; color:white; padding:4px 10px;"
        )
        banner_layout = QHBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(0, 0, 0, 0)
        self._update_label = QLabel()
        self._update_label.setOpenExternalLinks(True)
        self._update_label.setStyleSheet("color:white;")
        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFlat(True)
        dismiss_btn.setStyleSheet("color:white; font-weight:bold;")
        dismiss_btn.clicked.connect(lambda: self._update_banner.setVisible(False))
        banner_layout.addWidget(self._update_label, 1)
        banner_layout.addWidget(dismiss_btn)
        main_layout.addWidget(self._update_banner)

        # Main content area with sidebar and stacked widget
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Sidebar
        self._sidebar = QFrame()
        self._sidebar.setFrameShape(QFrame.Shape.StyledPanel)
        self._sidebar.setMaximumWidth(180)
        self._sidebar.setMinimumWidth(180)
        sidebar_colors = _get_sidebar_colors()
        self._sidebar.setStyleSheet(f"background-color: {sidebar_colors['bg']}; border-right: 1px solid {sidebar_colors['border']};")
        
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(5, 10, 5, 10)
        sidebar_layout.setSpacing(4)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Create sidebar buttons with Material Design Icons
        self._overview_btn = SidebarButton("Overview", "overview", self)
        self._fido2_btn = SidebarButton("FIDO2", "fido2", self)
        self._admin_btn = SidebarButton("Admin", "admin", self)
        self._settings_btn = SidebarButton("Settings", "settings", self)
        
        # Dynamic buttons (will be shown/hidden)
        self._piv_btn = SidebarButton("PIV", "piv", self)
        self._gpg_btn = SidebarButton("OpenPGP", "gpg", self)
        self._vault_btn = SidebarButton("Vault", "vault", self)
        
        # Add static buttons to sidebar
        sidebar_layout.addWidget(self._overview_btn)
        sidebar_layout.addWidget(self._fido2_btn)
        
        # Container for dynamic buttons (PIV, OpenPGP, Vault)
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
        
        # Select first tab by default (tabs set up in _setup_tabs, called next)
        # Deferred to after _setup_tabs via _setup_connections → done at end of __init__

    def _setup_tabs(self) -> None:
        """Setup tabs and link them to sidebar buttons.

        All tabs are added to the stacked widget once at startup in a fixed
        order. Dynamic tabs (PIV, OpenPGP, Vault) are always present but their
        sidebar buttons are hidden until the device reports them as available.
        Navigation uses setCurrentWidget() so indices never matter.
        """
        self._overview_tab = OverviewTab()
        self._fido2_tab = Fido2Tab()
        self._piv_tab = PivTab()
        self._gpg_tab = GpgTab()
        self._vault_tab = VaultTab()
        self._admin_tab = AdminTab()
        self._settings_tab = SettingsTab(browser_server=self._browser_server)

        # Add every tab once — order determines visual stacking, not navigation
        for tab in (
            self._overview_tab,
            self._fido2_tab,
            self._piv_tab,
            self._gpg_tab,
            self._vault_tab,
            self._admin_tab,
            self._settings_tab,
        ):
            self._stacked_widget.addWidget(tab)

        # widget → button mapping used by _select_tab and _set_tabs_enabled
        self._tab_buttons = {
            self._overview_tab: self._overview_btn,
            self._fido2_tab:    self._fido2_btn,
            self._piv_tab:      self._piv_btn,
            self._gpg_tab:      self._gpg_btn,
            self._vault_tab:    self._vault_btn,
            self._admin_tab:    self._admin_btn,
            self._settings_tab: self._settings_btn,
        }

        # Connect every button — captures widget reference, not index
        for tab, btn in self._tab_buttons.items():
            btn.clicked.connect(lambda checked, t=tab: self._select_tab(t))

        # Dynamic buttons live in the container but start hidden
        for btn in (
            self._piv_btn,
            self._gpg_btn,
            self._vault_btn,
        ):
            self._dynamic_buttons_layout.addWidget(btn)
            btn.setVisible(False)

        self._set_tabs_enabled(False)

    def _select_tab(self, tab: QWidget, remember: bool = True) -> None:
        """Show a tab and mark its sidebar button as active."""
        self._stacked_widget.setCurrentWidget(tab)
        for t, btn in self._tab_buttons.items():
            btn.setChecked(t is tab)
        if remember:
            self._last_active_tab = tab
            self._pending_restore_tab = None

    def _can_restore_tab(self, tab: Optional[QWidget]) -> bool:
        if tab is None:
            return False
        if tab is self._settings_tab:
            return True
        if self._current_device is None:
            return False
        btn = self._tab_buttons.get(tab)
        if btn is None:
            return False
        if btn in (self._piv_btn, self._gpg_btn, self._vault_btn):
            return btn.isVisible() and btn.isEnabled()
        return btn.isEnabled()

    def _try_restore_tab(self) -> bool:
        if not self._can_restore_tab(self._pending_restore_tab):
            return False
        self._select_tab(self._pending_restore_tab, remember=False)
        self._pending_restore_tab = None
        return True

    def _setup_connections(self) -> None:
        self._device_monitor.device_connected.connect(self._on_device_connected)
        self._device_monitor.device_disconnected.connect(self._on_device_disconnected)
        self._device_monitor.device_error.connect(self._on_device_monitor_error)
        self._refresh_button.clicked.connect(self._refresh_devices)
        self._admin_tab.reconnect_expected.connect(self._begin_expected_reconnect_scan)
        self._admin_tab.reconnect_prepare.connect(self._prepare_for_reconnect)
        self._admin_tab.isp_done.connect(self._device_monitor.resume_monitoring)
        self._admin_tab.variant_detected.connect(self._overview_tab.on_variant_detected)
        self._overview_tab.check_variant_requested.connect(self._admin_tab.trigger_check_variant)
        self._piv_tab.piv_availability.connect(self._on_piv_availability)
        self._gpg_tab.gpg_availability.connect(self._on_gpg_availability)
        self._vault_tab.vault_available.connect(self._on_vault_availability)

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
        check_update_action = QAction("Check for Updates", self)
        check_update_action.triggered.connect(self._manual_check_for_updates)
        help_menu.addAction(check_update_action)
        help_menu.addSeparator()
        help_menu.addAction(about_action)

    # -------------------------------------------------------------------------
    # Event handlers

    def _on_device_connected(self, device: SoloDevice) -> None:
        if self._current_device is not None:
            self._overview_tab.clear_device()
            self._fido2_tab.clear_device()
            self._piv_tab.clear_device()
            self._gpg_tab.clear_device()
            self._vault_tab.clear_device()
            self._admin_tab.clear_device()
            self._settings_tab.clear_device()

        self._current_device = device

        info = device.get_info()
        if info.mode.value == "regular":
            self._device_manager.start(device)

        in_bootloader = info.mode.value == "bootloader"
        suffix = " (Bootloader)" if in_bootloader else ""
        fw_str = format_firmware_version(info.firmware_version)

        is_locked = getattr(device, "is_locked", None)
        # Only trust admin-reported locked (True); admin cannot confirm unlocked
        # without ISP — show nothing until ISP confirms via "Check Variant".
        variant_label = " (locked)" if is_locked is True else ""
        self._device_label.setText(f"Solo 2{variant_label}{suffix} - fw {fw_str}")
        self._variant_help_lbl.setVisible(is_locked is True and not in_bootloader)

        self._set_tabs_enabled(True)

        self._overview_tab.set_device(device)
        self._fido2_tab.set_device(device)
        self._piv_tab.set_device(device)
        self._gpg_tab.set_device(device)
        self._vault_tab.set_device(device)
        self._admin_tab.set_device(device)
        self._settings_tab.set_device(device)
        self._pending_restore_tab = self._last_active_tab
        self._try_restore_tab()
        self._reconnect_scan_attempts_remaining = 0
        self._refresh_timer.stop()

        mode_label = "Bootloader" if in_bootloader else "Normal"
        self._status_bar.showMessage(f"Solo 2 connected ({mode_label} mode)")

    def _on_device_disconnected(self, path: str) -> None:
        if self._current_device is None:
            return
        self._last_active_tab = self._stacked_widget.currentWidget()
        self._pending_restore_tab = None
        self._current_device = None
        self._device_label.setText("No device connected")
        self._variant_help_lbl.setVisible(False)
        self._set_tabs_enabled(False)
        self._status_bar.showMessage("Device disconnected")

        self._overview_tab.clear_device()
        self._fido2_tab.clear_device()
        self._piv_tab.clear_device()
        self._gpg_tab.clear_device()
        self._vault_tab.clear_device()
        self._admin_tab.clear_device()
        self._settings_tab.clear_device()

        # Hide dynamic tab buttons
        self._piv_btn.setVisible(False)
        self._gpg_btn.setVisible(False)
        self._vault_btn.setVisible(False)

        # Stop DeviceManager
        self._device_manager.stop()

        # Return to overview
        self._select_tab(self._overview_tab, remember=False)

    def _on_tab_changed(self, index: int) -> None:
        if self._current_device is None:
            return
        tab = self._stacked_widget.widget(index)
        if tab is self._fido2_tab:
            self._fido2_tab.refresh_state()

    def _refresh_devices(self) -> None:
        self._status_bar.showMessage("Refreshing devices...")
        self._device_monitor.refresh_devices()
        if self._current_device is not None:
            self._fido2_tab.refresh_state()
        QTimer.singleShot(500, self._show_current_device_status)

    def _show_current_device_status(self) -> None:
        if self._current_device is None:
            self._status_bar.showMessage("No device connected")
            return
        try:
            info = self._current_device.get_info()
            mode_label = "Bootloader" if info.mode.value == "bootloader" else "Normal"
            self._status_bar.showMessage(f"Solo 2 connected ({mode_label} mode)")
        except Exception:
            self._status_bar.showMessage("Ready")

    def _prepare_for_reconnect(self) -> None:
        """Tell the device monitor to expect a disconnect, without clearing worker state.
        Pauses all monitor polling to prevent the ISP check racing with discovery.
        """
        self._device_monitor.prepare_for_expected_reconnect()
        self._device_monitor.pause_monitoring()

    def _begin_expected_reconnect_scan(self) -> None:
        if self._current_device is not None:
            self._on_device_disconnected(getattr(self._current_device, "path", ""))
        self._device_monitor.prepare_for_expected_reconnect()
        self._reconnect_scan_attempts_remaining = 24
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _run_expected_reconnect_scan(self) -> None:
        if self._reconnect_scan_attempts_remaining <= 0:
            self._refresh_timer.stop()
            return
        self._reconnect_scan_attempts_remaining -= 1
        self._device_monitor.refresh_devices()

    def _on_device_monitor_error(self, path: str, error: str) -> None:
        QMessageBox.warning(self, "Device Error", f"Device error: {error}")
        self._status_bar.showMessage(f"Device error: {error}")

    def _on_piv_availability(self, available: bool) -> None:
        self._piv_btn.setVisible(available)
        self._piv_btn.setEnabled(available and self._current_device is not None)
        if self._pending_restore_tab is self._piv_tab:
            if available:
                self._try_restore_tab()
            else:
                self._pending_restore_tab = None

    def _on_gpg_availability(self, available: bool) -> None:
        self._gpg_btn.setVisible(available)
        self._gpg_btn.setEnabled(available and self._current_device is not None)
        if self._pending_restore_tab is self._gpg_tab:
            if available:
                self._try_restore_tab()
            else:
                self._pending_restore_tab = None

    def _on_vault_availability(self, available: bool) -> None:
        self._vault_btn.setVisible(available)
        self._vault_btn.setEnabled(available and self._current_device is not None)
        if self._pending_restore_tab is self._vault_tab:
            if available:
                self._try_restore_tab()
            else:
                self._pending_restore_tab = None

    def _set_tabs_enabled(self, enabled: bool) -> None:
        """Enable/disable sidebar buttons. Dynamic buttons only enabled when visible."""
        dynamic_btns = (
            self._piv_btn,
            self._gpg_btn,
            self._vault_btn,
        )
        for tab, btn in self._tab_buttons.items():
            if btn is self._settings_btn:
                btn.setEnabled(True)
                continue
            if btn in dynamic_btns:
                btn.setEnabled(enabled and btn.isVisible())
            else:
                btn.setEnabled(enabled)

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
            f"SoloKeys GUI v{__version__}\n\n"
            "Platform-independent GUI for managing SoloKeys Solo 2 FIDO2 tokens.\n\n"
            "License: GNU GPL v3.0\n"
            "Source: https://github.com/leetronics/solo2-gui",
        )

    def _check_for_updates(self) -> None:
        """Trigger an update check (startup auto-check, silent when up to date)."""
        QMetaObject.invokeMethod(self._update_worker, "check", Qt.ConnectionType.QueuedConnection)

    def _manual_check_for_updates(self) -> None:
        """Trigger an update check from the Help menu (shows 'Up to date' feedback)."""
        self._manual_update_check = True
        self._status_bar.showMessage("Checking for updates…", 3000)
        QMetaObject.invokeMethod(self._update_worker, "check", Qt.ConnectionType.QueuedConnection)

    def _on_update_checked(self, tag: str, url: str, is_newer: bool) -> None:
        manual = self._manual_update_check
        self._manual_update_check = False
        if is_newer:
            self._update_label.setText(
                f"Update available: <b>{tag}</b> — "
                f'<a href="{url}" style="color:white;">Download</a>'
            )
            self._update_banner.setVisible(True)
        elif manual:
            self._status_bar.showMessage("Up to date", 4000)

    def _shutdown_app(self) -> None:
        """Stop all background workers before Qt destroys their QThread objects."""
        if self._shutdown_done:
            return
        self._shutdown_done = True

        self._refresh_timer.stop()
        self._cleanup_tab_workers()

        if self._update_thread and self._update_thread.isRunning():
            self._update_thread.quit()
            self._update_thread.wait()

        self._device_manager.stop()
        self._device_monitor.stop_monitoring()

        if self._browser_server:
            self._browser_server.stop()

    def _cleanup_tab_workers(self) -> None:
        """Best-effort cleanup for tab-owned QThreads."""
        cleanup_calls = (
            self._overview_tab._cleanup_firmware_worker,
            self._fido2_tab._cleanup_worker,
            self._piv_tab._cleanup_worker,
            self._gpg_tab._cleanup_worker,
            self._vault_tab._cleanup_worker,
            self._admin_tab._cleanup_worker,
            self._settings_tab._cleanup_worker,
        )
        for cleanup in cleanup_calls:
            try:
                cleanup()
            except Exception:
                pass
