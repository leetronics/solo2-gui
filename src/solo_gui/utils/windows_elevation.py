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


def _build_restart_command(extra_args: list[str] | None = None) -> tuple[str, str]:
    """Build the command line used for an elevated restart on Windows."""
    extra_args = extra_args or []
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = subprocess.list2cmdline([*sys.argv[1:], *extra_args])
    else:
        executable = sys.executable
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
