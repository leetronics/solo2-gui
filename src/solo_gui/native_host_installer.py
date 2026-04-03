"""
Register / unregister the SoloKeys Secrets native messaging host with Chrome/Chromium.

Works in four deployment scenarios:
  1. System-wide Linux package — uses packaged manifests in /etc/.../native-messaging-hosts
     and a stable host wrapper in /usr/bin.
  2. Frozen PyInstaller app — looks for a sibling 'solokeys-secrets-host[.exe]' binary
     next to the main executable.  No Python needed on the user's machine.
  3. Installed via pip/poetry — uses the 'solokeys-secrets-host' console-script entry point.
  4. Running from source — creates a thin wrapper script that calls the module.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

HOST_NAME = "com.solokeys.secrets"
OBSOLETE_HOST_NAMES = ("com.solokeys.totp",)

# Fixed Chrome extension ID derived from the bundled RSA public key in
# chrome-solokeys-totp/manifest.json ("key" field).  Must stay in sync.
EXTENSION_ID = "pfcbbbbhhjkecdmjadjgphfpphmgjkpj"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"


# ---------------------------------------------------------------------------
# Platform paths
# ---------------------------------------------------------------------------


def _manifest_filename(host_name: str) -> str:
    return f"{host_name}.json"

def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "solokeys-gui"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        return Path.home() / ".local" / "share" / "solokeys-gui"


def _get_manifest_dirs() -> list[Path]:
    """Return per-user directories where Chrome/Chromium look for manifests."""
    if sys.platform == "win32":
        # On Windows the manifest path is stored in the registry; we write the
        # JSON file to our data dir and register its path.
        return [_get_data_dir()]
    elif sys.platform == "darwin":
        return [
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts",
            Path.home() / "Library" / "Application Support" / "Chromium" / "NativeMessagingHosts",
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome Canary" / "NativeMessagingHosts",
        ]
    else:
        return [
            Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts",
            Path.home() / ".config" / "chromium" / "NativeMessagingHosts",
            # Flatpak
            Path.home() / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome" / "NativeMessagingHosts",
            Path.home() / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium" / "NativeMessagingHosts",
        ]


def _get_system_manifest_dirs() -> list[Path]:
    """Return system-wide manifest directories for packaged Linux installs."""
    if sys.platform in {"win32", "darwin"}:
        return []
    return [
        Path("/etc/opt/chrome/native-messaging-hosts"),
        Path("/etc/chromium/native-messaging-hosts"),
        Path("/etc/chromium-browser/native-messaging-hosts"),
    ]


# ---------------------------------------------------------------------------
# Find the native host executable
# ---------------------------------------------------------------------------

def find_native_host_exe() -> Optional[str]:
    """
    Return the absolute path to the native host executable, or None if not found.

    Search order:
      1. Sibling binary next to the frozen main executable (PyInstaller release).
      2. 'solokeys-secrets-host' on PATH (pip/poetry install).
      3. A wrapper script we create next to the current file.
    """
    # 1. Frozen app: look for the sibling binary in the same directory
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        name = "solokeys-secrets-host.exe" if sys.platform == "win32" else "solokeys-secrets-host"
        sibling = exe_dir / name
        if sibling.exists():
            return str(sibling)

    # 2. Installed entry-point on PATH
    on_path = shutil.which("solokeys-secrets-host")
    if on_path:
        return on_path

    # 3. Create a wrapper that calls the module with the current Python interpreter
    return _create_wrapper()


def _create_wrapper() -> str:
    """Create a thin executable wrapper next to this module and return its path."""
    here = Path(__file__).parent.resolve()
    src_dir = here.parent.resolve()   # …/src  — the package root
    python = sys.executable

    if sys.platform == "win32":
        wrapper = here / "solokeys_secrets_host.bat"
        wrapper.write_text(
            f'@echo off\r\n'
            f'if defined PYTHONPATH (\r\n'
            f'    set PYTHONPATH={src_dir};%PYTHONPATH%\r\n'
            f') else (\r\n'
            f'    set PYTHONPATH={src_dir}\r\n'
            f')\r\n'
            f'set SOLOKEYS_PATH=auto\r\n'
            f'"{python}" -m solo_gui.native_host %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = here / "solokeys_secrets_host.sh"
        wrapper.write_text(
            f'#!/bin/sh\n'
            f'export PYTHONPATH="{src_dir}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'export SOLOKEYS_PATH=auto\n'
            f'exec "{python}" -m solo_gui.native_host "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    return str(wrapper)


# ---------------------------------------------------------------------------
# Registration status
# ---------------------------------------------------------------------------

def is_registered() -> bool:
    """Return True if the native host manifest is installed and its path exists."""
    return registration_scope() != "none"


def registration_scope() -> str:
    """Return ``none``, ``user`` or ``system`` for the active registration."""
    if sys.platform == "win32":
        return "user" if _is_registered_windows(HOST_NAME) else "none"

    for directory in _get_manifest_dirs():
        if _manifest_dir_is_valid(directory):
            return "user"

    for directory in _get_system_manifest_dirs():
        if _manifest_dir_is_valid(directory):
            return "system"

    return "none"


def is_system_managed() -> bool:
    """Return True when the active registration comes from a system package."""
    return registration_scope() == "system"


def _manifest_dir_is_valid(directory: Path) -> bool:
    return _manifest_is_valid(directory / _manifest_filename(HOST_NAME), HOST_NAME)


def _is_registered_windows(host_name: str) -> bool:
    try:
        import winreg
        key_path = rf"Software\Google\Chrome\NativeMessagingHosts\{host_name}"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
        manifest_path_str, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return _manifest_is_valid(Path(manifest_path_str), host_name)
    except Exception:
        return False


def _manifest_is_valid(path: Path, expected_host_name: str) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") != expected_host_name:
            return False
        host_exe = data.get("path", "")
        if not host_exe or not Path(host_exe).exists():
            return False
        # Also check that the manifest is for the current extension ID.
        # A stale manifest with a different origin triggers silent re-registration.
        origins = data.get("allowed_origins", [])
        return EXTENSION_ORIGIN in origins
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install() -> tuple[bool, str]:
    """Register the native messaging host for the fixed extension ID.

    Returns (success, message).
    """
    if is_system_managed():
        return True, (
            "Native host is already installed system-wide by the Linux package.\n"
            "No per-user registration is needed."
        )

    host_exe = find_native_host_exe()
    if not host_exe:
        return False, "Could not locate the native host executable."

    try:
        if sys.platform == "win32":
            _install_windows(host_exe)
        else:
            _install_posix(host_exe)
        return True, f"Registered native host.\nHost: {HOST_NAME}\nPath: {host_exe}"
    except Exception as e:
        return False, f"Registration failed: {e}"


def _build_manifest(host_name: str, host_exe: str) -> dict:
    return {
        "name": host_name,
        "description": "SoloKeys Secrets native messaging host",
        "path": host_exe,
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN],
    }


def _install_posix(host_exe: str) -> None:
    errors = []
    for d in _get_manifest_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / _manifest_filename(HOST_NAME)).write_text(
                json.dumps(_build_manifest(HOST_NAME, host_exe), indent=2),
                encoding="utf-8",
            )
            for host_name in OBSOLETE_HOST_NAMES:
                (d / _manifest_filename(host_name)).unlink(missing_ok=True)
        except Exception as e:
            errors.append(f"{d}: {e}")
    if errors and len(errors) == len(_get_manifest_dirs()):
        raise RuntimeError("\n".join(errors))


def _install_windows(host_exe: str) -> None:
    import winreg

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / _manifest_filename(HOST_NAME)
    manifest_path.write_text(
        json.dumps(_build_manifest(HOST_NAME, host_exe), indent=2),
        encoding="utf-8",
    )

    reg_keys = [
        rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
        rf"Software\Chromium\NativeMessagingHosts\{HOST_NAME}",
    ]
    for key_path in reg_keys:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[installer] Registry warning for {key_path}: {e}")

    for host_name in OBSOLETE_HOST_NAMES:
        obsolete_path = data_dir / _manifest_filename(host_name)
        try:
            obsolete_path.unlink(missing_ok=True)
        except Exception:
            pass
        for key_path in [
            rf"Software\Google\Chrome\NativeMessagingHosts\{host_name}",
            rf"Software\Chromium\NativeMessagingHosts\{host_name}",
        ]:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def uninstall() -> tuple[bool, str]:
    """Remove the native messaging host registration. Returns (success, message)."""
    try:
        if is_system_managed():
            return (
                False,
                "The native host is installed system-wide by the Linux package.\n"
                "Remove the package or the system manifest files with administrator privileges.",
            )
        if sys.platform == "win32":
            _uninstall_windows()
        else:
            _uninstall_posix()
        return True, "Native host unregistered."
    except Exception as e:
        return False, f"Unregistration failed: {e}"


def _uninstall_posix() -> None:
    for d in _get_manifest_dirs():
        for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
            target = d / _manifest_filename(host_name)
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass


def _uninstall_windows() -> None:
    import winreg

    for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
        reg_keys = [
            rf"Software\Google\Chrome\NativeMessagingHosts\{host_name}",
            rf"Software\Chromium\NativeMessagingHosts\{host_name}",
        ]
        for key_path in reg_keys:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass

    for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
        manifest_path = _get_data_dir() / _manifest_filename(host_name)
        try:
            manifest_path.unlink(missing_ok=True)
        except Exception:
            pass
