"""App update-check worker for SoloKeys GUI."""

try:
    from importlib.metadata import version as _pkg_version
    APP_VERSION = _pkg_version("solo-gui")
except Exception:
    APP_VERSION = "0.1.0"

GITHUB_REPO = "leetronics/solo2-gui"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple:
    """'v1.2.3' → (1, 2, 3); returns (0,) on failure."""
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except Exception:
        return (0,)


from PySide6.QtCore import QObject, Signal, Slot
import requests


class UpdateCheckWorker(QObject):
    """Runs in a QThread; emits update_checked with (tag, url, is_newer)."""

    update_checked = Signal(str, str, bool)

    @Slot()
    def check(self):
        try:
            resp = requests.get(
                RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            tag = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            is_newer = _parse_version(tag) > _parse_version(APP_VERSION)
            self.update_checked.emit(tag, html_url, is_newer)
        except Exception:
            pass  # Silent failure
