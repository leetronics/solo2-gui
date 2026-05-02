# SoloKeys GUI

Desktop GUI for managing Solo 2 devices across Linux, macOS, and Windows.

## What It Does

- Detects connected devices and shows firmware, capabilities, and diagnostics
- Manages FIDO2 resident credentials and PIN state
- Checks for firmware updates and installs them through the GUI
- Manages TOTP / Secrets credentials, including touch- and PIN-protected entries
- Exposes PIV and OpenPGP management flows when smartcard support is available
- Includes admin and hardware-developer tabs for reboot, reset, provisioning, and attestation tasks
- Registers Chrome/Chromium and Firefox native messaging hosts for browser-based Vault integration
- Optional autostart on login

## Requirements

- Python 3.10 or newer
- `libusb` available on the host system
- PySide6 / Qt 6
- Optional: `pcscd` plus `pyscard` for PIV and OpenPGP features
  - Building `pyscard` on Linux also requires `libpcsclite-dev`

## Running From Source

```bash
git clone git@github.com:leetronics/solo2-gui.git
cd solo2-gui

python3 -m venv .venv
source .venv/bin/activate

# On Linux, install the system packages from the Linux section below first.
pip install -r requirements.txt

PYTHONPATH=src python -m solo_gui.main
```

`poetry install` also works in this repository if you prefer Poetry over a plain virtualenv.

If you are developing `solo2-python` and this GUI side by side, the GUI source tree also prefers a sibling checkout at `../solo2-python/src` during local source runs.

## Platform Notes

### Linux

For Linux, the preferred end-user paths are native packages: `.deb` for Debian/Ubuntu-style systems and `.rpm` for Fedora/openSUSE-style systems. These packages install bundled PyInstaller binaries, the launcher, native messaging host and udev rules. They do not require system Python modules or distro PySide6/Qt6 packages at runtime.

For everything else, run the application directly from a cloned repo.

The packaged Linux artifacts install the desktop launcher, the Chrome/Chromium and Firefox native messaging hosts, and the SoloKeys udev rules system-wide.

Install `libusb` and, if you want smartcard-backed features, `pcscd`. For source installs, building `pyscard` may also require the PC/SC development headers.

Example for Debian/Ubuntu:

```bash
sudo apt install -y python3-venv libusb-1.0-0
# Optional smartcard support:
sudo apt install -y pcscd libpcsclite-dev
```

The `.deb` and `.rpm` packages use the same PyInstaller payload model as the AppImage, but install it as regular system package files.

For non-root USB/HID access on systemd-logind desktops, install udev rules for
Solo 2 regular firmware and bootloader modes:

```bash
sudo tee /etc/udev/rules.d/70-solokeys.rules >/dev/null <<'EOF'
# Solo 2 regular firmware
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="beee", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="beee", TAG+="uaccess"

# Solo 2 bootloader
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="b000", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="b000", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw
sudo udevadm trigger --subsystem-match=usb
```

Unplug and replug the Solo 2 after installing or updating the rules. These rules
use `TAG+="uaccess"` instead of a `plugdev` group because distributions such as
openSUSE Tumbleweed do not create `plugdev` by default.

To install and run the GUI directly from a checkout:

```bash
git clone git@github.com:leetronics/solo2-gui.git
cd solo2-gui

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PYTHONPATH=src python -m solo_gui.main
```

To install the packaged Debian/Ubuntu build:

```bash
sudo apt install ./solokeys-gui_<version>_amd64.deb
```

After installing the package, unplug and replug the Solo 2 once so the fresh udev rules apply. Browser integration should already be registered system-wide for Chrome/Chromium and Firefox.

To install the packaged RPM build on Fedora/openSUSE-style systems:

```bash
sudo dnf install ./solokeys-gui-<version>-1.x86_64.rpm
```

The RPM uses the same bundled PyInstaller payload model as the `.deb`; it does not depend on the distro's Python or PySide6 packages at runtime.

To build the Linux packages locally:

```bash
./build_linux_deb.sh
./build_linux_rpm.sh
./build_linux_appimage.sh
```

The Debian package is written to `dist/` as `solokeys-gui_<version>_<arch>.deb`. The RPM package is written to `dist/` as `solokeys-gui-<version>-1.<arch>.rpm`. The AppImage is written to `dist/` as `SoloKeys-GUI-<version>-x86_64.AppImage`.

The AppImage registers the Chrome/Chromium and Firefox native messaging host per user. Browser manifests point to a stable wrapper under `~/.local/share/solokeys-gui/`, and that wrapper launches the AppImage in native-host mode. If you move the AppImage, start it once and the app will repair the browser registration. The AppImage does not install udev rules; install the rules manually or use the native `.deb`/`.rpm` package for system integration.

### macOS

Install `libusb` via Homebrew before running from source:

```bash
brew install libusb
```

To build a distributable app bundle and DMG:

```bash
./build_macos.sh
```

Artifacts are written to `dist/`.

By default this creates an ad-hoc signed local build. For a Developer ID signed
release build, provide a signing identity and notarization credentials:

```bash
export MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MACOS_NOTARIZE=1
export APPLE_NOTARY_KEY_PATH=/path/to/AuthKey_KEYID.p8
export APPLE_NOTARY_KEY_ID=KEYID
export APPLE_NOTARY_ISSUER_ID=ISSUER_UUID
./build_macos.sh
```

Signed release builds automatically register the browser native messaging host
from inside the signed `.app` bundle. Ad-hoc builds keep using a per-user copy
under `~/Library/Application Support/solokeys-gui/` to avoid local Gatekeeper
issues while developing.

The GitHub desktop artifact workflows can sign and notarize macOS builds when
these repository secrets are configured:

- `APPLE_CERTIFICATE_P12`: base64-encoded Developer ID Application `.p12`
- `APPLE_CERTIFICATE_PASSWORD`: password for that `.p12`
- `APPLE_DEVELOPER_ID_APPLICATION`: full codesign identity string
- `APPLE_NOTARY_KEY_BASE64`: base64-encoded App Store Connect API `.p8` key
- `APPLE_NOTARY_KEY_ID`: App Store Connect API key ID
- `APPLE_NOTARY_ISSUER_ID`: App Store Connect issuer UUID

### Windows

Install Python 3.10+ and make sure `libusb-1.0.dll` is available. The build script looks for it in:

- `C:\libusb\MS64\dll\libusb-1.0.dll`
- `C:\tools\libusb\bin\libusb-1.0.dll`
- `%LIBUSB_PATH%`

To build the Windows package:

```bat
build_windows.bat
```

The primary distributable is produced as an installer in `dist\installer\`.

The build also creates an intermediate PyInstaller app folder in `dist\SoloKeys GUI\`. That folder is packaged into the installer together with the native messaging host helper.

The GitHub desktop artifact workflows can Authenticode-sign Windows builds when
these repository secrets are configured:

- `WINDOWS_CERTIFICATE_PFX`: base64-encoded standard code signing `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD`: password for that `.pfx`

When configured, the build signs the GUI executable, the native messaging host,
and the final Inno Setup installer. Local builds can use the same path by setting
`WINDOWS_CODESIGN_CERT_PATH` and `WINDOWS_CODESIGN_CERT_PASSWORD` before running
`build_windows.bat`.

## Browser Integration

The application can register a native messaging host named `com.solokeys.secrets` for Chrome/Chromium and Firefox. On startup it automatically ensures that both browser registrations exist, repairs stale per-user registrations, and leaves valid system-wide Linux package registrations unchanged. The same action is available in `Settings -> Browser`.

Linux native packages install the native host manifests system-wide, so in-app registration is mainly relevant for source installs and other unpackaged local runs.

The native host supports two modes:

- Forward requests to the running GUI over a local socket
- Fall back to direct HID access when the GUI is not available

## Privacy and Signing

- Privacy policy: [`PRIVACY.md`](PRIVACY.md)
- Third-party notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
- SignPath readiness notes: [`docs/signpath-readiness.md`](docs/signpath-readiness.md)

## Development

### Versioning

The central release version lives in `pyproject.toml` under `[tool.poetry].version`.

- Normal source runs and release builds use that version.
- The `Build Desktop Artifacts` CI workflow overrides it with the short Git commit hash for build artifacts.
- The `Release Desktop Artifacts` workflow uses the central `pyproject.toml` version and publishes assets to tag `v<version>`.

### Project Layout

```text
src/solo_gui/            Main application package
src/solo_gui/views/      Main window and tab UI
src/solo_gui/workers/    Background workers for device operations
src/solo_gui/models/     Device abstraction and monitoring
src/solo_gui/utils/      Autostart, USB monitoring, helpers
solokeys_gui.spec        PyInstaller spec for the desktop app
native_host.spec         PyInstaller spec for the native host helper
build_macos.sh           macOS packaging script
build_linux_deb.sh       Debian/Ubuntu packaging script
build_linux_rpm.sh       Fedora/openSUSE-style RPM packaging script
build_windows.bat        Windows packaging script
packaging/linux/         Linux desktop, native-host, udev and package build assets
installer_windows.iss    Inno Setup script for the Windows installer
test_*.py                Current pytest-based checks in the repo root
```

## License

This project is licensed under either the Apache License 2.0 or the MIT license,
at your option. See `LICENSE`, `LICENSE-APACHE`, and `LICENSE-MIT`.
