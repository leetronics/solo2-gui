# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the SoloKeys Secrets native messaging host.
#
# Produces a single-file binary called 'solokeys-secrets-host' (or .exe on Windows).
# This binary is placed alongside the main solokeys-gui executable and requires
# no Python installation on the end user's machine.
#
# Build:
#   pyinstaller native_host.spec
#
# The output lands in dist/solokeys-secrets-host[.exe].

import sys

a = Analysis(
    ['src/solo_gui/native_host.py'],
    pathex=['src'],   # so PyInstaller can resolve the solo_gui package
    binaries=[],
    datas=[],
    hiddenimports=[
        'hid',
        # Our own modules (lazy-imported inside _handle_direct)
        'solo_gui.device_transport',
        'solo_gui.hid_backend',
        'solo_gui.oath_bridge',
        # fido2 core
        'fido2',
        'fido2.cbor',
        'fido2.ctap2',
        'fido2.ctap2.base',
        'fido2.ctap2.pin',
        # fido2 HID — platform backends are conditionally imported;
        # list all so the binary works on each target OS.
        'fido2.hid',
        'fido2.hid.base',
        'fido2.hid.linux',
        'fido2.hid.macos',
        'fido2.hid.windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy GUI dependencies; fido2 IS required for direct HID path.
        'PySide6', 'pyusb', 'requests',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='solokeys-secrets-host',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # must be console — Chrome reads stdout/stdin
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
