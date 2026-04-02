"""
Register / unregister the SoloKeys Secrets native messaging host with Chrome/Chromium.

Works in three deployment scenarios:
  1. Frozen PyInstaller app — looks for a sibling 'solokeys-secrets-host[.exe]' binary
     next to the main executable.  No Python needed on the user's machine.
  2. Installed via pip/poetry — uses the 'solokeys-secrets-host' console-script entry point.
  3. Running from source — creates a thin wrapper script that calls the module.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

HOST_NAME = "com.solokeys.secrets"
_MANIFEST_FILENAME = f"{HOST_NAME}.json"

# Fixed Chrome extension ID derived from the bundled RSA public key in
# chrome-solokeys-totp/manifest.json ("key" field).  Must stay in sync.
EXTENSION_ID = "pfcbbbbhhjkecdmjadjgphfpphmgjkpj"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"


# ---------------------------------------------------------------------------
# Platform paths
# ---------------------------------------------------------------------------

def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "solokeys-gui"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        return Path.home() / ".local" / "share" / "solokeys-gui"


def _get_manifest_dirs() -> list[Path]:
    """Return the directories where Chrome/Chromium look for native host manifests."""
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
    if sys.platform == "win32":
        return _is_registered_windows()
    else:
        first_dir = _get_manifest_dirs()[0]
        manifest_path = first_dir / _MANIFEST_FILENAME
        return _manifest_is_valid(manifest_path)


def _is_registered_windows() -> bool:
    try:
        import winreg
        key_path = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
        manifest_path_str, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return _manifest_is_valid(Path(manifest_path_str))
    except Exception:
        return False


def _manifest_is_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
    host_exe = find_native_host_exe()
    if not host_exe:
        return False, "Could not locate the native host executable."

    manifest = {
        "name": HOST_NAME,
        "description": "SoloKeys Secrets native messaging host",
        "path": host_exe,
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN],
    }

    try:
        if sys.platform == "win32":
            _install_windows(manifest)
        else:
            _install_posix(manifest)
        return True, f"Registered native host.\nHost: {host_exe}"
    except Exception as e:
        return False, f"Registration failed: {e}"


def _install_posix(manifest: dict) -> None:
    errors = []
    for d in _get_manifest_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / _MANIFEST_FILENAME).write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
        except Exception as e:
            errors.append(f"{d}: {e}")
    if errors and len(errors) == len(_get_manifest_dirs()):
        raise RuntimeError("\n".join(errors))


def _install_windows(manifest: dict) -> None:
    import winreg

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / _MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

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


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def uninstall() -> tuple[bool, str]:
    """Remove the native messaging host registration. Returns (success, message)."""
    try:
        if sys.platform == "win32":
            _uninstall_windows()
        else:
            _uninstall_posix()
        return True, "Native host unregistered."
    except Exception as e:
        return False, f"Unregistration failed: {e}"


def _uninstall_posix() -> None:
    for d in _get_manifest_dirs():
        target = d / _MANIFEST_FILENAME
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass


def _uninstall_windows() -> None:
    import winreg

    reg_keys = [
        rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
        rf"Software\Chromium\NativeMessagingHosts\{HOST_NAME}",
    ]
    for key_path in reg_keys:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass

    manifest_path = _get_data_dir() / _MANIFEST_FILENAME
    try:
        manifest_path.unlink(missing_ok=True)
    except Exception:
        pass
