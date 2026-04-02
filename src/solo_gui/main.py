#!/usr/bin/env python3
"""Main entry point for the SoloKeys GUI application."""

import os
import signal
import sys
import configparser
from pathlib import Path

# Add src directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir.parent))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer, Qt
from solo_gui.views.main_window import MainWindow
from solo_gui.browser_server import BrowserServer
from solo_gui import native_host_installer

_ICON_RESOURCES_DIR = current_dir / "resources"


def _is_dark_mode() -> bool:
    force_mode = os.environ.get("SOLOKEYSGUI_THEME", "").lower()
    if force_mode == "dark":
        return True
    if force_mode == "light":
        return False

    color_scheme = QApplication.styleHints().colorScheme()
    if color_scheme == Qt.ColorScheme.Dark:
        return True
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


def _get_icon_path() -> Path:
    if sys.platform == "win32":
        if _is_dark_mode():
            return _ICON_RESOURCES_DIR / "icon-dark.ico"
        return _ICON_RESOURCES_DIR / "icon-light.ico"
    if _is_dark_mode():
        return _ICON_RESOURCES_DIR / "logo-dark.png"
    else:
        return _ICON_RESOURCES_DIR / "logo-light.png"


def main() -> None:
    """Main entry point for the application."""
    app = QApplication(sys.argv)
    app.setApplicationName("SoloKeys GUI")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("SoloKeys")
    app.setQuitOnLastWindowClosed(True)

    icon_path = _get_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Allow Ctrl+C in the terminal to quit the app.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    browser_server = BrowserServer()
    browser_server.start()
    app.aboutToQuit.connect(browser_server.stop)

    window = MainWindow(browser_server=browser_server)
    window.show()

    # Silently register the native messaging host on first run (or if stale).
    if not native_host_installer.is_registered():
        QTimer.singleShot(500, _auto_register_host)

    return app.exec()


def _auto_register_host() -> None:
    success, msg = native_host_installer.install()
    if not success:
        QMessageBox.warning(
            None,
            "Native Host Registration Failed",
            f"Could not register the native messaging host:\n\n{msg}\n\n"
            "You can retry in Settings → Browser.",
        )


if __name__ == "__main__":
    sys.exit(main())
