"""Autostart utility for cross-platform support.

Handles adding/removing the application from system autostart:
- Windows: Registry (HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run)
- macOS: LaunchAgents plist file
- Linux: Desktop entry in ~/.config/autostart
"""

import os
import sys
import platform
from pathlib import Path
from typing import Optional


class AutostartManager:
    """Manages application autostart settings across platforms."""

    def __init__(self, app_name: str = "SoloKeys GUI"):
        self.app_name = app_name
        self.app_id = app_name.lower().replace(" ", "-")
        self._executable_path = self._get_executable_path()

    def _get_executable_path(self) -> str:
        """Get the path to the current executable/script."""
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller bundle
            return sys.executable
        else:
            # Running as Python script
            return sys.executable + " " + os.path.abspath(sys.argv[0])

    def is_enabled(self) -> bool:
        """Check if autostart is currently enabled."""
        system = platform.system()
        if system == "Windows":
            return self._is_enabled_windows()
        elif system == "Darwin":
            return self._is_enabled_macos()
        else:  # Linux
            return self._is_enabled_linux()

    def enable(self) -> bool:
        """Enable autostart. Returns True on success."""
        system = platform.system()
        if system == "Windows":
            return self._enable_windows()
        elif system == "Darwin":
            return self._enable_macos()
        else:  # Linux
            return self._enable_linux()

    def disable(self) -> bool:
        """Disable autostart. Returns True on success."""
        system = platform.system()
        if system == "Windows":
            return self._disable_windows()
        elif system == "Darwin":
            return self._disable_macos()
        else:  # Linux
            return self._disable_linux()

    def toggle(self) -> bool:
        """Toggle autostart state. Returns new state (True = enabled)."""
        if self.is_enabled():
            self.disable()
            return False
        else:
            self.enable()
            return True

    # Windows implementation
    def _is_enabled_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                value, _ = winreg.QueryValueEx(key, self.app_name)
                return value == self._executable_path
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _enable_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, self._executable_path)
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _disable_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            try:
                winreg.DeleteValue(key, self.app_name)
            except FileNotFoundError:
                pass  # Already disabled
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    # macOS implementation
    def _is_enabled_macos(self) -> bool:
        plist_path = Path.home() / "Library/LaunchAgents" / f"{self.app_id}.plist"
        return plist_path.exists()

    def _enable_macos(self) -> bool:
        try:
            plist_path = Path.home() / "Library/LaunchAgents" / f"{self.app_id}.plist"
            plist_path.parent.mkdir(parents=True, exist_ok=True)

            # Get the executable path for macOS
            if getattr(sys, 'frozen', False):
                # PyInstaller bundle
                exe_path = sys.executable
            else:
                # Python script - use python interpreter
                exe_path = sys.executable

            plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.app_id}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe_path}</string>'''

            if not getattr(sys, 'frozen', False):
                plist_content += f'''
        <string>{os.path.abspath(sys.argv[0])}</string>'''

            plist_content += f'''
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>'''

            plist_path.write_text(plist_content)

            # Load the launch agent
            os.system(f"launchctl load {plist_path}")
            return True
        except Exception:
            return False

    def _disable_macos(self) -> bool:
        try:
            plist_path = Path.home() / "Library/LaunchAgents" / f"{self.app_id}.plist"
            if plist_path.exists():
                os.system(f"launchctl unload {plist_path}")
                plist_path.unlink()
            return True
        except Exception:
            return False

    # Linux implementation
    def _is_enabled_linux(self) -> bool:
        desktop_path = Path.home() / ".config/autostart" / f"{self.app_id}.desktop"
        return desktop_path.exists()

    def _enable_linux(self) -> bool:
        try:
            desktop_path = Path.home() / ".config/autostart" / f"{self.app_id}.desktop"
            desktop_path.parent.mkdir(parents=True, exist_ok=True)

            # Get the executable path
            if getattr(sys, 'frozen', False):
                exec_line = sys.executable
            else:
                exec_line = f"{sys.executable} {os.path.abspath(sys.argv[0])}"

            # Try to find an icon
            icon_path = self._find_icon_path()

            desktop_content = f'''[Desktop Entry]
Type=Application
Name={self.app_name}
Exec={exec_line}
Icon={icon_path if icon_path else "application-x-executable"}
Comment=SoloKeys Solo 2 GUI Manager
X-GNOME-Autostart-enabled=true
Hidden=false
NoDisplay=false
Terminal=false
'''

            desktop_path.write_text(desktop_content)
            desktop_path.chmod(0o755)
            return True
        except Exception:
            return False

    def _disable_linux(self) -> bool:
        try:
            desktop_path = Path.home() / ".config/autostart" / f"{self.app_id}.desktop"
            if desktop_path.exists():
                desktop_path.unlink()
            return True
        except Exception:
            return False

    def _find_icon_path(self) -> Optional[str]:
        """Try to find the application icon path."""
        # Check for icon in resources
        possible_paths = [
            Path(__file__).parent.parent / "resources" / "icon.png",
            Path(__file__).parent.parent.parent / "resources" / "icon.png",
        ]
        for path in possible_paths:
            if path.exists():
                return str(path)
        return None
