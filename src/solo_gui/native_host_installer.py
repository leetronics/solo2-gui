"""
Register / unregister the SoloKeys Vault native messaging host for Chromium and Firefox.

Works in four deployment scenarios:
  1. System-wide Linux package — uses packaged manifests in the browser-specific
     native-messaging directories and a stable host wrapper in /usr/bin.
  2. Frozen PyInstaller app — looks for a sibling native host helper next to
     the main executable. On macOS this is an onedir helper copied per-user so
     Chrome does not trigger PyInstaller onefile Python.framework extraction.
  3. Installed via pip/poetry — uses the 'solokeys-secrets-host' console-script
     entry point.
  4. Running from source — creates a thin wrapper script in the user's app data
     directory that calls the module with the current Python interpreter.
"""

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Optional

HOST_NAME = "com.solokeys.secrets"
OBSOLETE_HOST_NAMES = ("com.solokeys.totp",)
APPIMAGE_NATIVE_HOST_ARG = "--native-host"
APPIMAGE_WRAPPER_MARKER = "# SoloKeys GUI AppImage native host wrapper"
MACOS_HOST_MODE_MARKER = ".solokeys-native-host-mode"
MACOS_HOST_MODE_APP = "app"
MACOS_HOST_MODE_COPY = "copy"

CHROMIUM = "chromium"
FIREFOX = "firefox"
BROWSER_LABELS = {
    CHROMIUM: "Chrome/Chromium",
    FIREFOX: "Firefox",
}
BROWSER_KEYS = (CHROMIUM, FIREFOX)

# Fixed browser extension IDs. Must stay in sync with the extension manifests.
CHROMIUM_EXTENSION_ID = "pfcbbbbhhjkecdmjadjgphfpphmgjkpj"
CHROMIUM_EXTENSION_ORIGIN = f"chrome-extension://{CHROMIUM_EXTENSION_ID}/"
FIREFOX_EXTENSION_ID = "solokeys-vault@solokeys.dev"


def _manifest_filename(browser_key: str, host_name: str) -> str:
    if sys.platform == "win32":
        suffix = "chromium" if browser_key == CHROMIUM else "firefox"
        return f"{host_name}.{suffix}.json"
    return f"{host_name}.json"


def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "solokeys-gui"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "solokeys-gui"
    return Path.home() / ".local" / "share" / "solokeys-gui"


def _get_manifest_dirs(browser_key: str) -> list[Path]:
    if sys.platform == "win32":
        return [_get_data_dir()]

    if browser_key == FIREFOX:
        if sys.platform == "darwin":
            return [
                Path.home() / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts",
            ]
        return [
            Path.home() / ".mozilla" / "native-messaging-hosts",
        ]

    if sys.platform == "darwin":
        return _get_macos_chromium_manifest_dirs()

    return [
        Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts",
        Path.home() / ".config" / "chromium" / "NativeMessagingHosts",
        Path.home() / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome" / "NativeMessagingHosts",
        Path.home() / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium" / "NativeMessagingHosts",
    ]


def _get_macos_chromium_manifest_dirs() -> list[Path]:
    app_support = Path.home() / "Library" / "Application Support"
    default_profile = app_support / "Google" / "Chrome"
    profile_roots = [
        default_profile,
        app_support / "Google" / "Chrome Beta",
        app_support / "Google" / "Chrome Dev",
        app_support / "Google" / "Chrome Canary",
        app_support / "Google" / "Chrome for Testing",
        app_support / "Chromium",
        app_support / "BraveSoftware" / "Brave-Browser",
        app_support / "BraveSoftware" / "Brave-Browser-Beta",
        app_support / "BraveSoftware" / "Brave-Browser-Nightly",
        app_support / "Microsoft Edge",
        app_support / "Microsoft Edge Beta",
        app_support / "Microsoft Edge Dev",
        app_support / "Microsoft Edge Canary",
        app_support / "Vivaldi",
        app_support / "Vivaldi Snapshot",
        Path.home() / ".chrome-debug-profile",
    ]

    return [
        profile_root / "NativeMessagingHosts"
        for profile_root in profile_roots
        if profile_root == default_profile or profile_root.exists()
    ]


def _get_system_manifest_dirs(browser_key: str) -> list[Path]:
    if sys.platform in {"win32", "darwin"}:
        return []

    if browser_key == FIREFOX:
        return [
            Path("/usr/lib/mozilla/native-messaging-hosts"),
            Path("/usr/lib64/mozilla/native-messaging-hosts"),
        ]

    return [
        Path("/etc/opt/chrome/native-messaging-hosts"),
        Path("/etc/chromium/native-messaging-hosts"),
        Path("/etc/chromium-browser/native-messaging-hosts"),
    ]


def _get_windows_reg_keys(browser_key: str, host_name: str) -> list[str]:
    if browser_key == FIREFOX:
        return [rf"Software\Mozilla\NativeMessagingHosts\{host_name}"]
    return [
        rf"Software\Google\Chrome\NativeMessagingHosts\{host_name}",
        rf"Software\Chromium\NativeMessagingHosts\{host_name}",
    ]


def _get_wrapper_path() -> Path:
    if sys.platform == "win32":
        return _get_data_dir() / "solokeys-secrets-host.bat"
    return _get_data_dir() / "solokeys-secrets-host"


def _get_appimage_path() -> Optional[Path]:
    if not sys.platform.startswith("linux"):
        return None
    if not getattr(sys, "frozen", False):
        return None

    value = os.environ.get("APPIMAGE", "").strip()
    if not value:
        return None

    path = Path(value)
    if not path.is_absolute():
        path = path.resolve(strict=False)

    if not path.exists() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def _appimage_wrapper_contents(appimage_path: Path) -> str:
    appimage = shlex.quote(str(appimage_path))
    native_host_arg = shlex.quote(APPIMAGE_NATIVE_HOST_ARG)
    return (
        "#!/bin/sh\n"
        f"{APPIMAGE_WRAPPER_MARKER}\n"
        "export SOLOKEYS_PATH=auto\n"
        f"exec {appimage} {native_host_arg} \"$@\"\n"
    )


def _appimage_wrapper_is_current(wrapper: Path, appimage_path: Path) -> bool:
    if not _host_exe_is_valid(wrapper):
        return False
    try:
        return wrapper.read_text(encoding="utf-8") == _appimage_wrapper_contents(
            appimage_path
        )
    except Exception:
        return False


def _create_appimage_wrapper(appimage_path: Path) -> str:
    wrapper = _get_wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(_appimage_wrapper_contents(appimage_path), encoding="utf-8")
    wrapper.chmod(0o755)
    return str(wrapper)


def _get_macos_host_install_dir() -> Path:
    return _get_data_dir() / "native-host"


def _get_macos_host_install_exe() -> Path:
    return _get_macos_host_install_dir() / _native_host_binary_name()


def _get_macos_native_host_mode() -> str:
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return MACOS_HOST_MODE_COPY

    marker = _get_macos_bundle_resources_dir() / MACOS_HOST_MODE_MARKER
    try:
        value = marker.read_text(encoding="utf-8").strip().lower()
    except Exception:
        return MACOS_HOST_MODE_COPY

    if value == MACOS_HOST_MODE_APP:
        return MACOS_HOST_MODE_APP
    return MACOS_HOST_MODE_COPY


def _get_macos_bundle_resources_dir() -> Path:
    return Path(sys.executable).parent.parent / "Resources"


def _native_host_binary_name() -> str:
    if sys.platform == "win32":
        return "solokeys-secrets-host.exe"
    return "solokeys-secrets-host"


def _host_exe_is_valid(host_exe: str | Path) -> bool:
    path = Path(host_exe)
    if not path.exists() or not path.is_file():
        return False
    if sys.platform != "win32" and not os.access(path, os.X_OK):
        return False
    return True


def _clear_macos_quarantine(path: Path) -> None:
    if sys.platform != "darwin":
        return

    paths = [path]
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            root_path = Path(root)
            paths.extend(root_path / name for name in dirs)
            paths.extend(root_path / name for name in files)

    for candidate in paths:
        try:
            os.removexattr(candidate, "com.apple.quarantine")
        except (AttributeError, OSError):
            pass


def _find_frozen_native_host_exe() -> Optional[Path]:
    exe_dir = Path(sys.executable).parent
    name = _native_host_binary_name()

    for onedir_exe in (
        _get_macos_bundle_resources_dir() / name / name,
        exe_dir / name / name,
    ):
        if _host_exe_is_valid(onedir_exe):
            return onedir_exe

    sibling_exe = exe_dir / name
    if _host_exe_is_valid(sibling_exe):
        return sibling_exe

    return None


def _install_frozen_macos_host(source: Path) -> Optional[str]:
    name = _native_host_binary_name()
    if source.parent.name != name:
        return None

    target_dir = _get_macos_host_install_dir()
    target = _get_macos_host_install_exe()
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        if target_dir.exists():
            if target_dir.is_dir():
                shutil.rmtree(target_dir)
            else:
                target_dir.unlink()
        shutil.copytree(source.parent, target_dir, symlinks=True)
        target.chmod(0o755)
        _clear_macos_quarantine(target_dir)
    except Exception:
        return None

    if _host_exe_is_valid(target):
        return str(target)
    return None


def find_native_host_exe(create_wrapper: bool = True) -> Optional[str]:
    """
    Return the absolute path to the native host executable, or None if not found.
    """
    appimage = _get_appimage_path()
    if appimage is not None:
        wrapper = _get_wrapper_path()
        if create_wrapper:
            if not _appimage_wrapper_is_current(wrapper, appimage):
                return _create_appimage_wrapper(appimage)
            return str(wrapper)
        if _appimage_wrapper_is_current(wrapper, appimage):
            return str(wrapper)
        return None

    if getattr(sys, "frozen", False):
        source = _find_frozen_native_host_exe()
        if sys.platform == "darwin":
            if _get_macos_native_host_mode() == MACOS_HOST_MODE_APP:
                return str(source) if source is not None else None
            installed = _get_macos_host_install_exe()
            if create_wrapper and source is not None:
                return _install_frozen_macos_host(source)
            if _host_exe_is_valid(installed):
                return str(installed)
            return None

        if source is not None:
            return str(source)
        # A frozen GUI executable cannot be used as "python -m solo_gui.native_host".
        # Packaged builds must bundle the dedicated native-host helper binary.
        return None

    on_path = shutil.which("solokeys-secrets-host")
    if on_path and _host_exe_is_valid(on_path):
        return on_path

    wrapper = _get_wrapper_path()
    if wrapper.exists() and _host_exe_is_valid(wrapper):
        return str(wrapper)
    if not create_wrapper:
        return None
    return _create_wrapper()


def _create_wrapper() -> str:
    here = Path(__file__).parent.resolve()
    src_dir = here.parent.resolve()
    python = sys.executable
    wrapper = _get_wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
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
        wrapper.write_text(
            f'#!/bin/sh\n'
            f'export PYTHONPATH="{src_dir}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'export SOLOKEYS_PATH=auto\n'
            f'exec "{python}" -m solo_gui.native_host "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    return str(wrapper)


def registration_statuses() -> dict[str, dict]:
    statuses: dict[str, dict] = {}
    for browser_key in BROWSER_KEYS:
        statuses[browser_key] = {
            "label": BROWSER_LABELS[browser_key],
            "scope": _registration_scope(browser_key),
            "needs_repair": _needs_repair(browser_key),
        }
    return statuses


def is_registered() -> bool:
    statuses = registration_statuses()
    return all(
        status["scope"] != "none" and not status["needs_repair"]
        for status in statuses.values()
    )


def registration_scope() -> str:
    scopes = {status["scope"] for status in registration_statuses().values()}
    if scopes == {"system"}:
        return "system"
    if scopes == {"none"}:
        return "none"
    return "user"


def is_system_managed() -> bool:
    statuses = registration_statuses()
    return all(status["scope"] == "system" for status in statuses.values())


def needs_repair() -> bool:
    return any(status["needs_repair"] for status in registration_statuses().values())


def _registration_scope(browser_key: str) -> str:
    if sys.platform == "win32":
        return "user" if _is_registered_windows(browser_key, HOST_NAME) else "none"

    for directory in _get_manifest_dirs(browser_key):
        if _manifest_dir_is_valid(browser_key, directory):
            return "user"

    for directory in _get_system_manifest_dirs(browser_key):
        if _manifest_dir_is_valid(browser_key, directory):
            return "system"

    return "none"


def _needs_repair(browser_key: str) -> bool:
    scope = _registration_scope(browser_key)
    if _has_valid_system_manifest(browser_key) and _has_user_manifest_overrides(browser_key):
        return True
    if scope == "none":
        return False
    if scope != "user":
        return False

    expected_host_exe = find_native_host_exe(create_wrapper=False)
    return any(
        _manifest_needs_repair(
            directory / _manifest_filename(browser_key, HOST_NAME),
            browser_key,
            expected_host_exe,
        )
        for directory in _get_manifest_dirs(browser_key)
    )


def _manifest_needs_repair(
    manifest_path: Path,
    browser_key: str,
    expected_host_exe: Optional[str],
) -> bool:
    if not _manifest_is_valid(manifest_path, HOST_NAME, browser_key):
        return True

    registered_host_exe = _get_manifest_host_exe(manifest_path)
    if not registered_host_exe:
        return True
    if expected_host_exe:
        return not _paths_match(expected_host_exe, registered_host_exe)
    if getattr(sys, "frozen", False):
        return True
    return not _paths_match(str(_get_wrapper_path()), registered_host_exe)


def _manifest_dir_is_valid(browser_key: str, directory: Path) -> bool:
    return _manifest_is_valid(
        directory / _manifest_filename(browser_key, HOST_NAME),
        HOST_NAME,
        browser_key,
    )


def _has_valid_system_manifest(browser_key: str) -> bool:
    return any(_manifest_dir_is_valid(browser_key, directory) for directory in _get_system_manifest_dirs(browser_key))


def _has_user_manifest_overrides(browser_key: str) -> bool:
    if sys.platform == "win32":
        return False

    for directory in _get_manifest_dirs(browser_key):
        for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
            manifest_path = directory / _manifest_filename(browser_key, host_name)
            if manifest_path.exists():
                return True
    return False


def _is_registered_windows(browser_key: str, host_name: str) -> bool:
    try:
        manifest_path = _get_registered_manifest_path(browser_key, host_name)
        return manifest_path is not None and _manifest_is_valid(manifest_path, host_name, browser_key)
    except Exception:
        return False


def _get_registered_manifest_path(browser_key: str, host_name: str) -> Optional[Path]:
    if sys.platform == "win32":
        try:
            import winreg

            for key_path in _get_windows_reg_keys(browser_key, host_name):
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
                    manifest_path_str, _ = winreg.QueryValueEx(key, "")
                    winreg.CloseKey(key)
                    return Path(manifest_path_str)
                except FileNotFoundError:
                    continue
            return None
        except Exception:
            return None

    for directory in _get_manifest_dirs(browser_key):
        manifest_path = directory / _manifest_filename(browser_key, host_name)
        if manifest_path.exists():
            return manifest_path

    for directory in _get_system_manifest_dirs(browser_key):
        manifest_path = directory / _manifest_filename(browser_key, host_name)
        if manifest_path.exists():
            return manifest_path

    return None


def _get_registered_host_exe(browser_key: str, host_name: str) -> Optional[str]:
    manifest_path = _get_registered_manifest_path(browser_key, host_name)
    if manifest_path is None or not manifest_path.exists():
        return None
    return _get_manifest_host_exe(manifest_path)


def _get_manifest_host_exe(manifest_path: Path) -> Optional[str]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    host_exe = data.get("path", "")
    return host_exe or None


def _paths_match(left: str, right: str) -> bool:
    try:
        left_norm = os.path.normcase(str(Path(left).resolve(strict=False)))
    except Exception:
        left_norm = os.path.normcase(os.path.abspath(left))
    try:
        right_norm = os.path.normcase(str(Path(right).resolve(strict=False)))
    except Exception:
        right_norm = os.path.normcase(os.path.abspath(right))
    return left_norm == right_norm


def _expected_permissions(browser_key: str) -> tuple[str, list[str]]:
    if browser_key == FIREFOX:
        return "allowed_extensions", [FIREFOX_EXTENSION_ID]
    return "allowed_origins", [CHROMIUM_EXTENSION_ORIGIN]


def _manifest_is_valid(path: Path, expected_host_name: str, browser_key: str) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") != expected_host_name:
            return False
        host_exe = data.get("path", "")
        if not host_exe or not _host_exe_is_valid(host_exe):
            return False

        permissions_key, expected_values = _expected_permissions(browser_key)
        manifest_values = data.get(permissions_key, [])
        return manifest_values == expected_values
    except Exception:
        return False


def install() -> tuple[bool, str]:
    host_exe = find_native_host_exe()
    if not host_exe:
        return False, "Could not locate the native host executable."

    messages: list[str] = []
    errors: list[str] = []

    for browser_key in BROWSER_KEYS:
        label = BROWSER_LABELS[browser_key]
        system_scope = _has_valid_system_manifest(browser_key)
        scope = _registration_scope(browser_key)
        repair = _needs_repair(browser_key)

        if system_scope:
            try:
                removed = _remove_user_manifest_overrides(browser_key)
                if removed:
                    removed_lines = "\n".join(f"  • {path}" for path in removed)
                    messages.append(
                        f"{label}: already installed system-wide.\n"
                        f"Removed conflicting per-user manifests:\n{removed_lines}"
                    )
                else:
                    messages.append(f"{label}: already installed system-wide.")
            except Exception as exc:
                errors.append(f"{label}: could not repair conflicting per-user manifests: {exc}")
            continue

        if scope == "user" and not repair:
            messages.append(f"{label}: already registered.")
            continue

        try:
            if sys.platform == "win32":
                _install_windows(host_exe, browser_key)
            else:
                _install_posix(host_exe, browser_key)
            if scope == "user":
                messages.append(
                    f"{label}: repaired native host registration.\n"
                    f"Host: {HOST_NAME}\nPath: {host_exe}"
                )
            else:
                messages.append(
                    f"{label}: registered native host.\n"
                    f"Host: {HOST_NAME}\nPath: {host_exe}"
                )
        except Exception as exc:
            errors.append(f"{label}: registration failed: {exc}")

    message = "\n\n".join(messages + errors).strip()
    return not errors, message or "No browser registrations were changed."


def _build_manifest(browser_key: str, host_name: str, host_exe: str) -> dict:
    manifest = {
        "name": host_name,
        "description": "SoloKeys Vault native messaging host",
        "path": host_exe,
        "type": "stdio",
    }
    permissions_key, expected_values = _expected_permissions(browser_key)
    manifest[permissions_key] = expected_values
    return manifest


def _install_posix(host_exe: str, browser_key: str) -> None:
    errors: list[str] = []
    for directory in _get_manifest_dirs(browser_key):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / _manifest_filename(browser_key, HOST_NAME)).write_text(
                json.dumps(_build_manifest(browser_key, HOST_NAME, host_exe), indent=2),
                encoding="utf-8",
            )
            for host_name in OBSOLETE_HOST_NAMES:
                (directory / _manifest_filename(browser_key, host_name)).unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{directory}: {exc}")
    if errors and len(errors) == len(_get_manifest_dirs(browser_key)):
        raise RuntimeError("\n".join(errors))


def _remove_user_manifest_overrides(browser_key: str) -> list[str]:
    removed: list[str] = []
    errors: list[str] = []
    for directory in _get_manifest_dirs(browser_key):
        for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
            target = directory / _manifest_filename(browser_key, host_name)
            if not target.exists():
                continue
            try:
                target.unlink()
                removed.append(str(target))
            except Exception as exc:
                errors.append(f"{target}: {exc}")
    if errors:
        raise RuntimeError("\n".join(errors))
    return removed


def _install_windows(host_exe: str, browser_key: str) -> None:
    import winreg

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / _manifest_filename(browser_key, HOST_NAME)
    manifest_path.write_text(
        json.dumps(_build_manifest(browser_key, HOST_NAME, host_exe), indent=2),
        encoding="utf-8",
    )

    for key_path in _get_windows_reg_keys(browser_key, HOST_NAME):
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        winreg.CloseKey(key)

    for host_name in OBSOLETE_HOST_NAMES:
        obsolete_path = data_dir / _manifest_filename(browser_key, host_name)
        obsolete_path.unlink(missing_ok=True)
        for key_path in _get_windows_reg_keys(browser_key, host_name):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass


def uninstall() -> tuple[bool, str]:
    messages: list[str] = []
    errors: list[str] = []

    for browser_key in BROWSER_KEYS:
        label = BROWSER_LABELS[browser_key]
        scope = _registration_scope(browser_key)

        if scope == "system":
            messages.append(f"{label}: system-wide registration left unchanged.")
            continue

        try:
            if sys.platform == "win32":
                _uninstall_windows(browser_key)
            else:
                _uninstall_posix(browser_key)
            messages.append(f"{label}: user registration removed.")
        except Exception as exc:
            errors.append(f"{label}: unregistration failed: {exc}")

    message = "\n\n".join(messages + errors).strip()
    return not errors, message or "No browser registrations were removed."


def _uninstall_posix(browser_key: str) -> None:
    for directory in _get_manifest_dirs(browser_key):
        for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
            target = directory / _manifest_filename(browser_key, host_name)
            target.unlink(missing_ok=True)


def _uninstall_windows(browser_key: str) -> None:
    import winreg

    for host_name in (HOST_NAME, *OBSOLETE_HOST_NAMES):
        for key_path in _get_windows_reg_keys(browser_key, host_name):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except FileNotFoundError:
                pass

        manifest_path = _get_data_dir() / _manifest_filename(browser_key, host_name)
        manifest_path.unlink(missing_ok=True)
