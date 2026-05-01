# Third-Party Notices

SoloKeys GUI is distributed under `MIT OR Apache-2.0`.

The packaged desktop artifacts may include the following open-source runtime
components. This list tracks the direct runtime dependencies declared by this
project; generated PyInstaller payloads may also include transitive dependencies
such as `certifi`, `charset-normalizer`, `idna`, `urllib3`, `cffi`, and
`pycparser`.

| Component | Purpose | License family |
|-----------|---------|----------------|
| `solo2` | Solo 2 hardware abstraction library | `MIT OR Apache-2.0` |
| `PySide6` / Qt for Python | GUI toolkit | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` |
| `fido2` | CTAP/FIDO2 protocol support | BSD-style |
| `pyusb` | USB backend support | BSD-style |
| `requests` | HTTPS requests for update checks/downloads | Apache-2.0 |
| `qtawesome` | Icon support | MIT |
| `pyscard` | PC/SC smartcard support | LGPL-style |
| `hidapi` / `cython-hidapi` | HID device enumeration/access | BSD-style |
| `cryptography` | Cryptographic primitives | Apache-2.0 OR BSD-style |
| `pywin32` | Windows native integration | PSF-style |
| `PyInstaller` bootloader | Packaged executable launcher | GPL-2.0-or-later with bootloader exception |
| Inno Setup | Windows installer builder | BSD-style |

## Bundled Firmware/Device Images

`src/solo_gui/resources/provisioner-minimal.bin` is bundled so the GUI can run
the Solo 2 FIDO2 self-attestation provisioning flow.

Known local metadata:

- size: `227324` bytes
- SHA-256: `51bbd12700cc1c0b577ca39749907c49130bba8bd2bf78d7e7e22d1d2efd41cf`
- introduced in this repository by commit
  `1e1f26f76cc25e5674a3d8a50e4478c1ab3978ab`
- source repository: `https://github.com/leetronics/solo2`
- source revision: `20421d1a8a61e6e0043bd7f0e9c9f977803801f6`
- source license: `MIT OR Apache-2.0`
- build directory: `runners/lpc55`
- build command:

  ```bash
  DEFMT_LOG=info cargo objcopy --release --no-default-features \
      --features board-solo2,develop-provisioner,format-filesystem,admin-app \
      -- -O binary /tmp/provisioner-minimal.bin
  ```

Before applying for third-party open-source code signing, verify and document:

- whether the exact binary can be reproduced byte-for-byte from the source
  revision above
- whether the binary should remain inside signed desktop installers

This documents the open-source provenance needed for a strict "all bundled
components are open source or system libraries" review. Byte-for-byte
reproducibility should still be verified before relying on this for third-party
code-signing approval.
