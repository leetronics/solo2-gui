#!/usr/bin/env python3
"""Main entry point for the SoloKeys GUI application."""

import signal
import sys
from pathlib import Path

# Add src directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from solo_gui.views.main_window import MainWindow

_ICON_PATH = current_dir / "resources" / "icon.png"


def main() -> None:
    """Main entry point for the application."""
    app = QApplication(sys.argv)
    app.setApplicationName("SoloKeys GUI")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("SoloKeys")

    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    # Allow Ctrl+C in the terminal to quit the app.
    # A periodic timer is needed so Python's signal handler gets a chance to run
    # while the Qt event loop is active.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
