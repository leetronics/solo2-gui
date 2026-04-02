# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the SoloKeys HID Proxy Windows service.
#
# Produces a single console executable: dist/solokeys-service.exe
# The service runs as LocalSystem and forwards HID calls to non-admin GUI processes.
#
# Build:
#   python -m PyInstaller --clean --noconfirm solokeys_service.spec

a = Analysis(
    ['src/solo_gui/win_hid_service.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'fido2',
        'fido2.hid',
        'fido2.hid.base',
        'fido2.hid.windows',
        'fido2.ctap',
        'win32serviceutil',
        'win32service',
        'win32event',
        'servicemanager',
        'pywintypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6',
        'pyusb',
        'requests',
        'PIL',
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
    name='solokeys-service',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
