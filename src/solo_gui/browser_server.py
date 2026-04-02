"""IPC server that lets the Chrome extension native-messaging bridge talk to solokeys-gui.

Uses platform-native local IPC:
  Linux  : AF_UNIX socket at ~/.local/share/solokeys-gui/secrets.sock
  macOS  : AF_UNIX socket at ~/Library/Application Support/solokeys-gui/secrets.sock
  Windows: named pipe at \\\\.\\pipe\\solokeys-secrets

The accept loop runs in a daemon thread; each connection spawns its own thread.
Security is enforced by Chrome via allowed_origins in the native messaging manifest
— no additional pairing or client approval is required here.
"""

import json
import logging
import os
import socket as _socket
import struct
import sys
import threading
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from solo_gui.device_manager import DeviceManager

_log = logging.getLogger("solo2device")


def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "solokeys-gui"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        return Path.home() / ".local" / "share" / "solokeys-gui"


_DATA_DIR = _get_data_dir()
_SOCKET_PATH = _DATA_DIR / "secrets.sock"
_PIPE_NAME = r"\\.\pipe\solokeys-secrets"


class BrowserServer(QObject):
    """Serves a local IPC endpoint for the Chrome extension native-messaging bridge."""

    # Emitted with an empty list (kept for settings_tab API compatibility)
    clients_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server_sock: Optional[_socket.socket] = None
        self._pipe_listener: Optional[Listener] = None

    # ------------------------------------------------------------------
    # Public API

    def start(self) -> None:
        if sys.platform == "win32":
            try:
                self._pipe_listener = Listener(address=_PIPE_NAME, family="AF_PIPE")
            except PermissionError as e:
                self._pipe_listener = None
                _log.warning("BrowserServer pipe unavailable on Windows: %s", e)
                print(f"[BrowserServer] Named pipe unavailable, browser IPC disabled: {e}")
                return
            except OSError as e:
                self._pipe_listener = None
                _log.warning("BrowserServer failed to open named pipe: %s", e)
                print(f"[BrowserServer] Failed to open named pipe, browser IPC disabled: {e}")
                return

            t = threading.Thread(target=self._accept_loop, daemon=True)
            t.start()
            print(f"[BrowserServer] Listening on named pipe {_PIPE_NAME}")
            return

        if not hasattr(_socket, "AF_UNIX"):
            print("[BrowserServer] AF_UNIX unavailable on this runtime; browser IPC disabled")
            self._server_sock = None
            return

        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass

        self._server_sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        self._server_sock.bind(str(_SOCKET_PATH))
        self._server_sock.listen(5)

        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        print(f"[BrowserServer] Listening on {_SOCKET_PATH}")

    def stop(self) -> None:
        if self._pipe_listener:
            try:
                self._pipe_listener.close()
            except Exception:
                pass
            self._pipe_listener = None
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        try:
            _SOCKET_PATH.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Accept loop (daemon thread)

    def _accept_loop(self) -> None:
        if self._pipe_listener is not None:
            while True:
                try:
                    conn = self._pipe_listener.accept()
                except (OSError, EOFError):
                    break
                t = threading.Thread(target=self._handle_pipe_connection,
                                     args=(conn,), daemon=True)
                t.start()
            return

        if self._server_sock is None:
            return
        while True:
            try:
                conn, _ = self._server_sock.accept()
            except OSError:
                break  # server socket was closed via stop()
            t = threading.Thread(target=self._handle_connection,
                                 args=(conn,), daemon=True)
            t.start()

    # ------------------------------------------------------------------
    # Per-connection handler (worker thread, no Qt objects)

    def _handle_pipe_connection(self, conn) -> None:
        with conn:
            try:
                msg = conn.recv()
            except Exception as e:
                try:
                    conn.send({"success": False, "error": f"Read error: {e}"})
                except Exception:
                    pass
                return
            response = self._handle_message(msg)
            try:
                conn.send(response)
            except Exception:
                pass

    def _handle_connection(self, sock: _socket.socket) -> None:
        with sock:
            sock.settimeout(5.0)
            try:
                msg = self._read_framed(sock)
            except Exception as e:
                try:
                    self._send_framed(sock, {"success": False, "error": f"Read error: {e}"})
                except Exception:
                    pass
                return
            response = self._handle_message(msg)
            try:
                self._send_framed(sock, response)
            except Exception:
                pass

    def _read_framed(self, sock: _socket.socket) -> dict:
        raw = self._recv_exactly(sock, 4)
        length = struct.unpack_from('<I', raw, 0)[0]
        body = self._recv_exactly(sock, length)
        return json.loads(body.decode())

    def _send_framed(self, sock: _socket.socket, msg: dict) -> None:
        data = json.dumps(msg).encode()
        sock.sendall(struct.pack('<I', len(data)) + data)

    @staticmethod
    def _recv_exactly(sock: _socket.socket, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("Connection closed by peer")
            buf += chunk
        return buf

    # ------------------------------------------------------------------
    # Message dispatch

    def _get_oath_bridge(self):
        if not hasattr(self, '_oath_bridge') or self._oath_bridge is None:
            from solo_gui.oath_bridge import OATHBridge
            self._oath_bridge = OATHBridge()
        return self._oath_bridge

    def _handle_message(self, msg: dict) -> dict:
        from solo_gui.oath_bridge import OATHTouchRequired, OATHPINRequired
        action = msg.get("action")
        bridge = self._get_oath_bridge()

        if action == "ping":
            return {"success": True}

        if action == "listCredentials":
            try:
                return {"success": True, "credentials": bridge.list_credentials()}
            except Exception as e:
                return {"success": False, "error": str(e)}

        if action == "listSecrets":
            try:
                return {"success": True, "credentials": bridge.list_secrets()}
            except Exception as e:
                return {"success": False, "error": str(e)}

        if action == "calculateOTP":
            try:
                return {"success": True, "otp": bridge.calculate_otp(msg["name"])}
            except OATHTouchRequired:
                return {"success": False, "error": "TOUCH_REQUIRED"}
            except OATHPINRequired:
                return {"success": False, "error": "PIN_REQUIRED"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        if action == "verifyPIN":
            return bridge.verify_pin(msg.get("pin", ""))

        if action == "setPIN":
            return bridge.set_pin(msg.get("pin", ""))

        if action == "changePIN":
            return bridge.change_pin(msg.get("oldPin", ""), msg.get("newPin", ""))

        if action == "addCredential":
            return bridge.add_credential(
                msg["name"], msg["secret"], msg.get("type", "TOTP"),
                msg.get("algorithm", "SHA1"), msg.get("digits", 6),
                msg.get("touchRequired", False), msg.get("pinProtected", False),
                login=msg.get("login"),
                password=msg.get("password"),
                metadata=msg.get("metadata"),
                password_only=msg.get("passwordOnly", False),
            )

        if action == "deleteCredential":
            return bridge.delete_credential(msg["name"])

        if action == "getPasswordEntry":
            return bridge.get_password_entry(msg["name"])

        if action == "updatePasswordEntry":
            return bridge.update_password_entry(
                msg["name"],
                new_name=msg.get("newName"),
                login=msg.get("login"),
                password=msg.get("password"),
                metadata=msg.get("metadata"),
            )

        return {"success": False, "error": f"Unknown action: {action}"}
