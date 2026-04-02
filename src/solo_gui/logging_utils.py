"""Application logging helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def get_log_path() -> Path:
    """Return the per-user debug log path."""
    if sys.platform == "win32":
        base = Path.home()
        local_appdata = Path(os.environ.get("LOCALAPPDATA", base / "AppData" / "Local"))
        data_dir = local_appdata / "solokeys-gui"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        data_dir = Path.home() / ".local" / "share" / "solokeys-gui"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "solokeys-debug.log"


def setup_logging() -> Path:
    """Configure file logging early in process startup."""
    log_path = get_log_path()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    target = str(log_path)
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == target:
            return log_path

    file_handler = logging.FileHandler(target, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger.addHandler(file_handler)
    root_logger.debug("Logging initialized: %s", log_path)
    return log_path
