"""SoloKeys GUI - Platform-independent GUI for managing SoloKeys Solo 2 FIDO2 tokens."""

import re
import sys
from pathlib import Path

_fallback = Path(__file__).resolve().parents[3] / "solo2-python" / "src"
if _fallback.exists() and str(_fallback) not in sys.path:
    sys.path.insert(0, str(_fallback))

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("solokeys-gui")
except Exception:
    _default = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    if _default.exists():
        match = re.search(
            r'^version\s*=\s*"([^"]+)"', _default.read_text(), re.MULTILINE
        )
        __version__ = match.group(1) if match else "0.0.0"
    else:
        __version__ = "0.0.0"

__author__ = "SoloKeys GUI Team"
