# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the SoloKeys Secrets native messaging host.
#
# Produces a native messaging host called 'solokeys-secrets-host' (or .exe on
# Windows). Windows keeps the single-file binary. macOS uses onedir because the
# onefile bootloader extracts Python.framework at runtime, which Gatekeeper can
# block when Chrome launches the host.
#
# Build:
#   pyinstaller native_host.spec
#
# The output lands in dist/solokeys-secrets-host[.exe] on Windows/Linux and
# dist/solokeys-secrets-host/solokeys-secrets-host on macOS.

import sys

a = Analysis(
    ['src/solo_gui/native_host.py'],
    pathex=['src'],   # so PyInstaller can resolve the solo_gui package
    binaries=[],
    datas=[],
    hiddenimports=[
        'hid',
        # Our own modules (lazy-imported inside _handle_direct)
        'solo2',
        'solo2.admin',
        'solo2.clients',
        'solo2.device',
        'solo2.discovery',
        'solo2.errors',
        'solo2.fido2',
        'solo2.hid_backend',
        'solo2.pcsc',
        'solo2.provisioner',
        'solo2.secrets',
        'solo2.transport',
        'solo_gui.device_transport',
        'solo_gui.hid_backend',
        'solo_gui.oath_bridge',
        'smartcard',
        'smartcard.System',
        'smartcard.Exceptions',
        'smartcard.CardConnection',
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

if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='solokeys-secrets-host',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,   # must be console — Chrome reads stdout/stdin
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
        name='solokeys-secrets-host',
    )

else:
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
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,   # must be console — Chrome reads stdout/stdin
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
