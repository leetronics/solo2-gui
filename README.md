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

For Linux, the preferred end-user paths are native packages: `.deb` for Debian/Ubuntu-style systems and `.rpm` for Fedora/openSUSE-style systems. These packages install the SoloKeys code, launcher, native messaging host and udev rules. Most Python dependencies (fido2, pyusb, qtawesome, pyscard, hidapi, etc.) are installed automatically via pip into a private `site-packages` directory at install time. Only PySide6 and `python3-pip` are expected from the distro.

For everything else, run the application directly from a cloned repo.

The packaged Linux artifacts install the desktop launcher, the Chrome/Chromium and Firefox native messaging hosts, and the SoloKeys udev rules system-wide.

Install `libusb` and, if you want smartcard-backed features, `pcscd`. For source installs, building `pyscard` may also require the PC/SC development headers.

Example for Debian/Ubuntu:

```bash
sudo apt install -y python3-venv libusb-1.0-0
# Optional smartcard support:
sudo apt install -y pcscd libpcsclite-dev
```

The `.deb` package installs Python dependencies (fido2, pyusb, qtawesome, pyscard, hidapi, etc.) automatically via pip at install time. Only PySide6 must come from the distro — it is declared as a package dependency and pulled in by `apt`. If the system Python is upgraded, the postinst hook automatically reinstalls the pip-managed packages.

For non-root HID access, install udev rules for SoloKeys devices:

```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="beee", MODE="0660", GROUP="plugdev"' \
    | sudo tee /etc/udev/rules.d/70-solokeys.rules
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="b000", MODE="0660", GROUP="plugdev"' \
    | sudo tee -a /etc/udev/rules.d/70-solokeys.rules
sudo udevadm control --reload-rules
sudo usermod -aG plugdev "$USER"
```

Log out and back in after changing group membership.

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

After installing the package, unplug and replug the SoloKey once so the fresh udev rules apply. Browser integration should already be registered system-wide for Chrome/Chromium and Firefox.

To install the packaged RPM build on Fedora/openSUSE-style systems:

```bash
sudo dnf install ./solokeys-gui-<version>-1.x86_64.rpm
```

The RPM uses the same pip-based install model as the `.deb`: Python dependencies are installed automatically via pip at install time, with only PySide6 expected from the distro. If your RPM-based distro lacks a PySide6 package, use the source-based path above instead.

To build the Linux packages locally:

```bash
./build_linux_deb.sh
./build_linux_rpm.sh
```

The Debian package is written to `dist/` as `solokeys-gui_<version>_<arch>.deb`. The RPM package is written to `dist/` as `solokeys-gui-<version>-1.<arch>.rpm`. For distros without a native package yet, the source-based path above remains the supported fallback.

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

## Browser Integration

The application can register a native messaging host named `com.solokeys.secrets` for Chrome/Chromium and Firefox. When the app starts, it attempts to register the host automatically if it is missing or stale, and the same action is available in `Settings -> Browser`.

Linux native packages install the native host manifests system-wide, so in-app registration is mainly relevant for source installs and other unpackaged local runs.

The native host supports two modes:

- Forward requests to the running GUI over a local socket
- Fall back to direct HID access when the GUI is not available

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

This project is licensed under the GNU General Public License v3.0. See `LICENSE`.
