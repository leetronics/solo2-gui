# SoloKeys GUI

Platform-independent GUI for managing SoloKeys Solo 2 FIDO2 tokens.

## Features

- Device detection and status monitoring
- FIDO2 credential management
- PIV (SSH/GPG) key management
- TOTP management (requires firmware changes)
- Firmware updates
- Cross-platform support (Windows, macOS, Linux)

## Requirements

- Python 3.10+
- Qt6 (included with PySide6)

## Installation

### Linux (from source)

```bash
# System dependencies
sudo apt install -y libusb-1.0-0

# Optional: PIV smartcard support
sudo apt install -y pcscd

# udev rule for non-root HID access
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="beee", MODE="0660", GROUP="plugdev"' \
    | sudo tee /etc/udev/rules.d/70-solokeys.rules
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="b000", MODE="0660", GROUP="plugdev"' \
    | sudo tee -a /etc/udev/rules.d/70-solokeys.rules
sudo udevadm control --reload-rules
sudo usermod -aG plugdev "$USER"
# Log out and back in for the group change to take effect

# Clone, install and run
git clone https://github.com/solokeys/solokeys-gui.git
cd solokeys-gui
pip install -r requirements.txt
# Optional: pip install pyscard  (for PIV tab)
PYTHONPATH=src python src/solo_gui/main.py
```

### macOS (pre-built binary)

Download `SoloKeys GUI-<version>.dmg` from the [Releases](../../releases) page,
open it and drag **SoloKeys GUI.app** to your Applications folder.

To build from source:

```bash
brew install libusb
./build_macos.sh
# App appears at dist/SoloKeys GUI.app
```

### Windows (pre-built binary)

Download `SoloKeys GUI-<version>.zip` from the [Releases](../../releases) page,
extract and run `SoloKeys GUI.exe`.

To build from source, install [libusb for Windows](https://github.com/libusb/libusb/releases)
and run `build_windows.bat`.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/solokeys/solokeys-gui.git
cd solokeys-gui

# Install dependencies using Poetry (recommended)
poetry install

# Or using pip
pip install -r requirements.txt

# Run the application
PYTHONPATH=src python src/solo_gui/main.py
```

## Project Structure

```
solokeys-gui/
├── src/solo_gui/           # Main application code
│   ├── models/             # Device abstraction and data models
│   ├── views/              # UI components and windows
│   ├── workers/            # Background thread workers
│   └── utils/              # Utility functions
├── tests/                  # Test suite
├── docs/                   # Documentation
└── packaging/              # Platform-specific packaging
```

## Development

### Code Style

- Uses Black for code formatting
- MyPy for type checking
- Conventional commit messages

### Testing

```bash
# Run tests
poetry run pytest

# Run tests with GUI
poetry run pytest --qt-no-capture
```

## License

[License to be determined]

## Contributing

[Contributing guidelines to be added]