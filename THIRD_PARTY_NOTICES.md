# Third-Party Notices

SoloKeys GUI is distributed under `MIT OR Apache-2.0`.

The packaged desktop artifacts may include the following open-source runtime
components. This list tracks the direct runtime dependencies declared by this
project; generated PyInstaller payloads may also include transitive dependencies
such as `certifi` (MPL-2.0), `charset-normalizer` (MIT), `idna` (BSD-3-Clause),
`urllib3` (MIT), `cffi` (MIT), and `pycparser` (BSD-3-Clause).

License audit (2026-08-02): the license metadata of every package in the
build environment was reviewed. All runtime components bundled into the
PyInstaller payloads are covered by OSI-approved open-source licenses; no
proprietary components are included. Copyleft components (PySide6/Qt under
LGPL-3.0, pyscard and libusb under LGPL-2.1-or-later) are dynamically linked
shared libraries that can be replaced by the user, satisfying the LGPL. The
GPL-licensed `pyinstaller` and `pyinstaller-hooks-contrib` are build tools;
only the PyInstaller bootloader ships in the artifacts, and it carries the
GPL bootloader exception for exactly this purpose. `hidapi` is dual-licensed
BSD-style/GPL-3.0 and is used under the BSD-style option.

| Component | Purpose | License family |
|-----------|---------|----------------|
| CPython runtime | Embedded Python interpreter | PSF-2.0 |
| `solo2` | Solo 2 hardware abstraction library | `MIT OR Apache-2.0` |
| `PySide6` / Qt for Python (ships Qt 6 libraries) | GUI toolkit | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (used under LGPL-3.0) |
| `fido2` | CTAP/FIDO2 protocol support | BSD-2-Clause (ships `public_suffix_list.dat`, MPL-2.0) |
| `pyusb` | USB backend support | BSD-style |
| `libusb` | USB access library (bundled on Windows/macOS, system library on Linux) | LGPL-2.1-or-later |
| `requests` | HTTPS requests for update checks/downloads | Apache-2.0 |
| `qtawesome` | Icon support | MIT (bundled icon fonts under OFL-1.1 / Apache-2.0 / MIT) |
| `pyscard` | PC/SC smartcard support | LGPL-2.1-or-later |
| `hidapi` / `cython-hidapi` | HID device enumeration/access | dual BSD-style / GPL-3.0 (used under the BSD-style option) |
| `cryptography` | Cryptographic primitives | Apache-2.0 OR BSD-style |
| `pywin32` | Windows native integration | PSF-style |
| `PyInstaller` bootloader | Packaged executable launcher | GPL-2.0-or-later with bootloader exception |
| Inno Setup | Windows installer builder | BSD-style (Inno Setup license) |

## Bundled Firmware/Device Images

`src/solo_gui/resources/provisioner-minimal.bin` is bundled so the GUI can run
the Solo 2 FIDO2 self-attestation provisioning flow.

Known local metadata:

- size: `227308` bytes
- SHA-256: `b5be58ce11d23e29cfa69ec9cc6f27f0c8d2af9d953f63d1bdbb2095e5973305`
- source repository: `https://github.com/leetronics/solo2`
- source revision: `20421d1a8a61e6e0043bd7f0e9c9f977803801f6`
- source license: `MIT OR Apache-2.0`
- build directory: `runners/lpc55`
- toolchain: Rust pinned by the repository's `rust-toolchain.toml`, plus
  `cargo-binutils`, `flip-link`, and `arm-none-eabi-gcc`
- build command (run from `runners/lpc55` of the source repository):

  ```bash
  DEFMT_LOG=info \
  RUSTFLAGS="-C linker=flip-link -C link-arg=-Tlink.x -C link-arg=-Tdefmt.x -Dwarnings \
    --remap-path-prefix=$HOME/.cargo=/cargo \
    --remap-path-prefix=$(git rev-parse --show-toplevel)=/build" \
  cargo objcopy --release --no-default-features \
      --features board-solo2,develop-provisioner,format-filesystem,admin-app \
      -- -O binary provisioner-minimal.bin
  ```

The build is byte-for-byte reproducible: the `--remap-path-prefix` flags strip
the build host's directories from embedded path strings, and independent builds
from different checkout locations produce exactly the SHA-256 above. Because
the `RUSTFLAGS` environment variable replaces the `rustflags` from the
repository's `.cargo/config.toml`, the linker flags are repeated in it.

The binary ships inside the signed desktop installers: it is open-source
firmware with the provenance documented above, required for provisioning FIDO2
self-attestation on hacker keys / developer builds, and bundling it keeps that
feature working offline. It is data to the desktop application — device
firmware, never executed on the host — so it is not itself Authenticode-signed;
its integrity is covered by the signed installer.

This documents the open-source provenance needed for a strict "all bundled
components are open source or system libraries" review.
