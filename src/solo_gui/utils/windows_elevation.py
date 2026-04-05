"""Windows elevation helpers for restarting the GUI as Administrator."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def is_windows_admin() -> bool:
    """Return True when the current process already runs elevated on Windows."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def can_restart_as_admin() -> bool:
    """Return True if the current platform supports an elevated restart."""
    return sys.platform == "win32" and not is_windows_admin()


def _resolve_restart_executable() -> str:
    """Prefer a windowed Python interpreter when restarting from source on Windows."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return str(executable)

    lower_name = executable.name.lower()
    if lower_name.startswith("python") or lower_name in {"py.exe", "pythond.exe"}:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)

    return str(executable)


def _build_restart_command(extra_args: list[str] | None = None) -> tuple[str, str]:
    """Build the command line used for an elevated restart on Windows."""
    extra_args = extra_args or []
    if getattr(sys, "frozen", False):
        executable = _resolve_restart_executable()
        params = subprocess.list2cmdline([*sys.argv[1:], *extra_args])
    else:
        executable = _resolve_restart_executable()
        params = subprocess.list2cmdline([sys.argv[0], *sys.argv[1:], *extra_args])
    return executable, params


def restart_as_admin() -> tuple[bool, str]:
    """Restart the current GUI process with Administrator rights on Windows."""
    if sys.platform != "win32":
        return False, "Administrator restart is only available on Windows."

    executable, params = _build_restart_command(
        [f"--wait-for-parent-pid={os.getpid()}"]
    )
    cwd = str(Path.cwd())

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            params or None,
            cwd,
            1,
        )
    except Exception as exc:
        return False, str(exc)

    if result <= 32:
        return False, f"Windows elevation failed with code {result}."

    return True, ""


def restart_as_admin_from_ui(parent=None) -> bool:
    """Trigger an elevated restart from the GUI and handle quit/error UI centrally."""
    ok, error = restart_as_admin()
    if ok:
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
        return True

    try:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            parent,
            "Restart Failed",
            f"Could not restart the GUI as Administrator:\n{error}",
        )
    except Exception:
        pass
    return False
