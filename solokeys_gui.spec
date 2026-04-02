# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SoloKeys GUI.

Produces:
  macOS  → dist/SoloKeys GUI.app  (+ DMG created by build_macos.sh)
  Windows → dist/SoloKeys GUI/    (onedir)

Build with:
  macOS:   ./build_macos.sh
  Windows: build_windows.bat
"""

import os
import re
import sys
import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
project_root = Path(SPECPATH)          # directory containing this .spec file
src_root     = project_root / "src"   # compensates for main.py sys.path hack

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
_version_match = re.search(r'^version\s*=\s*"([^"]+)"', (project_root / "pyproject.toml").read_text(), re.MULTILINE)
APP_VERSION = _version_match.group(1) if _version_match else "0.0.0"

# ---------------------------------------------------------------------------
# Platform-specific libusb detection
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    libusb_env = os.environ.get("LIBUSB_PATH", "")
    if libusb_env and Path(libusb_env).exists():
        libusb_path = libusb_env
    elif Path("/opt/homebrew/lib/libusb-1.0.0.dylib").exists():
        libusb_path = "/opt/homebrew/lib/libusb-1.0.0.dylib"   # Apple Silicon
    elif Path("/usr/local/lib/libusb-1.0.0.dylib").exists():
        libusb_path = "/usr/local/lib/libusb-1.0.0.dylib"      # Intel
    else:
        raise SystemExit(
            "libusb not found. Install with: brew install libusb\n"
            "Or set LIBUSB_PATH to the full path of libusb-1.0.0.dylib"
        )
    platform_binaries   = [(libusb_path, ".")]
    platform_rthooks    = ["rthooks/rthook_libusb_macos.py"]
    platform_excludes   = []

elif sys.platform == "win32":
    libusb_env = os.environ.get("LIBUSB_PATH", "")
    dll_candidates = [
        libusb_env,
        r"C:\libusb\MS64\dll\libusb-1.0.dll",
        r"C:\tools\libusb\bin\libusb-1.0.dll",
    ]
    libusb_path = next(
        (p for p in dll_candidates if p and Path(p).exists()), None
    )
    if libusb_path is None:
        raise SystemExit(
            "libusb-1.0.dll not found.\n"
            "Download from https://github.com/libusb/libusb/releases and set "
            "LIBUSB_PATH=<full path to libusb-1.0.dll>, or place the DLL at "
            r"C:\libusb\MS64\dll\libusb-1.0.dll"
        )
    platform_binaries   = [(libusb_path, ".")]
    platform_rthooks    = ["rthooks/rthook_libusb_windows.py"]
    platform_excludes   = []

else:
    # Linux — libusb is a system library; no bundling needed
    platform_binaries   = []
    platform_rthooks    = []
    platform_excludes   = []

# ---------------------------------------------------------------------------
# Common datas (platform-independent)
# ---------------------------------------------------------------------------
import fido2, certifi

datas = [
    # fido2 needs its public suffix list at runtime (fido2.rpid)
    (str(Path(fido2.__file__).parent / "public_suffix_list.dat"), "fido2"),
    # certifi CA bundle for HTTPS requests (firmware update downloads)
    (certifi.where(), "certifi"),
    # App icons (theme-aware with fallback)
    (str(src_root / "solo_gui" / "resources" / "logo-light.png"), "resources"),
    (str(src_root / "solo_gui" / "resources" / "logo-dark.png"), "resources"),
    (str(src_root / "solo_gui" / "resources" / "logo-square.png"), "resources"),
]

smartcard_hiddenimports = []
if importlib.util.find_spec("smartcard") is not None:
    smartcard_hiddenimports = [
        "smartcard",
        "smartcard.System",
        "smartcard.Exceptions",
        "smartcard.util",
        "smartcard.CardConnection",
        "smartcard.pcsc.PCSCContext",
        "smartcard.pcsc.PCSCCardConnection",
        "smartcard.scard",
        "smartcard.scard.scard",
    ]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(src_root / "solo_gui" / "main.py")],
    pathex=[str(src_root)],
    binaries=platform_binaries,
    datas=datas,
    hiddenimports=[
        "hid",
        # fido2 — platform HID backend selected via importlib at runtime
        "fido2.hid",
        "fido2.hid.linux",
        "fido2.hid.macos",
        "fido2.hid.windows",
        "fido2.cbor",
        "fido2.cose",
        "fido2.utils",
        "fido2.ctap2",
        "fido2.ctap2.base",
        "fido2.ctap2.extensions",
        "fido2.ctap2.pin",
        "fido2.webauthn",
        "fido2.attestation",
        # pyusb — backends selected lazily in usb.core
        "usb.backend.libusb0",
        "usb.backend.libusb1",
        "usb.backend.openusb",
        # requests stack — portions loaded lazily
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
        # cryptography — backend loaded via importlib
        "cryptography.hazmat.backends",
        "cryptography.hazmat.backends.openssl",
        # PySide6 extras used by the app
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtNetwork",
    ] + smartcard_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=platform_rthooks,
    excludes=[
        # Large PySide6 modules not used by this app
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtDataVisualization",
        "PySide6.QtCharts",
        "PySide6.QtLocation",
        "PySide6.QtPositioning",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTextToSpeech",
        "PySide6.QtVirtualKeyboard",
    ] + platform_excludes,
    noarchive=False,
    # UPX disabled: breaks macOS codesigning; causes AV false positives on Windows
    upx=False,
)

pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# macOS — .app bundle
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SoloKeys GUI",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="SoloKeys GUI",
    )

    app = BUNDLE(
        coll,
        name="SoloKeys GUI.app",
        icon=str(project_root / "src" / "solo_gui" / "resources" / "logo-square.png"),
        bundle_identifier="com.solokeys.solokeys-gui",
        version=APP_VERSION,
        info_plist={
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "SoloKeys GUI Contributors",
            "CFBundleDisplayName": "SoloKeys GUI",
            "CFBundleShortVersionString": APP_VERSION,
        },
    )

# ---------------------------------------------------------------------------
# Windows — onedir (avoids startup delay and AV flags of onefile)
# ---------------------------------------------------------------------------
elif sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SoloKeys GUI",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(project_root / "src" / "solo_gui" / "resources" / "icon-light.ico"),
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="SoloKeys GUI",
    )

# ---------------------------------------------------------------------------
# Linux — onedir (for completeness; primary method is source install)
# ---------------------------------------------------------------------------
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SoloKeys GUI",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="SoloKeys GUI",
    )
