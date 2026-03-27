# PyInstaller runtime hook — Windows
# Patches pyusb to load the bundled libusb DLL from the frozen bundle
# instead of searching PATH/System32 (which may not have it on end-user
# machines that haven't installed the libusb driver separately).

import os
import sys

_dll = os.path.join(sys._MEIPASS, "libusb-1.0.dll")
if os.path.exists(_dll):
    import usb.backend.libusb1 as _b
    _b.get_backend(find_library=lambda _ctx: _dll)
