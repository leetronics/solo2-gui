# PyInstaller runtime hook — macOS
# Patches pyusb to load the bundled libusb dylib from the frozen bundle
# instead of searching system library paths (which won't exist on end-user
# machines that don't have Homebrew/MacPorts installed).

import os
import sys

_lib = os.path.join(sys._MEIPASS, "libusb-1.0.0.dylib")
if os.path.exists(_lib):
    import usb.backend.libusb1 as _b
    _b.get_backend(find_library=lambda _ctx: _lib)
