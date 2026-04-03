#!/usr/bin/env python3
"""
SoloKeys Secrets Chrome Extension - Native Messaging Host

Hybrid bridge: tries the solokeys-gui Unix socket first (path 1), then falls
back to direct fido2 HID access (path 2) if the GUI is not running.

Control via SOLOKEYS_PATH environment variable:
  auto   (default) — try socket, fall back to direct HID
  socket            — socket only; return error if GUI not running
  direct            — direct HID only; skip socket even if GUI is running
"""

import json
import os
import struct
import sys
from multiprocessing.connection import Client
from pathlib import Path


def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "solokeys-gui"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "solokeys-gui"
    else:
        return Path.home() / ".local" / "share" / "solokeys-gui"


_SOCKET_PATH = _get_data_dir() / "secrets.sock"
_PIPE_NAME = r"\\.\pipe\solokeys-secrets"
_PATH_OVERRIDE = os.environ.get("SOLOKEYS_PATH", "auto").lower()


# ---------- Native messaging framing ----------

def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    length = struct.unpack('=I', raw)[0]
    return json.loads(sys.stdin.buffer.read(length).decode('utf-8'))


def send_message(msg):
    data = json.dumps(msg).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('=I', len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ---------- Path 1: GUI socket ----------

def _try_gui_socket(msg, retries: int = 2) -> tuple[dict | None, str | None]:
    """Forward msg to the solokeys-gui socket.

    Returns ``(response, fatal_error)``.
    ``response`` is the GUI result on success.
    ``fatal_error`` is set when we should not fall back to direct device access.
    """
    import socket as _socket
    import time

    for attempt in range(retries):
        try:
            if sys.platform == "win32":
                with Client(_PIPE_NAME, family="AF_PIPE") as conn:
                    conn.send(msg)
                    return conn.recv(), None

            if not hasattr(_socket, "AF_UNIX"):
                return None, None

            if not _SOCKET_PATH.exists():
                return None, None

            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect(str(_SOCKET_PATH))
                data = json.dumps(msg).encode()
                s.sendall(struct.pack('<I', len(data)) + data)

                raw_len = _recv_exactly(s, 4)
                length = struct.unpack('<I', raw_len)[0]
                response = json.loads(_recv_exactly(s, length).decode())
                # GUI responded successfully
                return response, None
        except PermissionError as exc:
            if sys.platform == "win32":
                return None, (
                    "SoloKeys GUI is running, but the browser bridge cannot access its "
                    f"Windows named pipe: {exc}. "
                    "Direct fallback is disabled to avoid conflicting access to the token."
                )
            if attempt < retries - 1:
                time.sleep(0.2)
            continue
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 5:
                return None, (
                    "SoloKeys GUI is running, but the browser bridge cannot access its "
                    f"Windows named pipe: {exc}. "
                    "Direct fallback is disabled to avoid conflicting access to the token."
                )
            if attempt < retries - 1:
                time.sleep(0.2)  # Brief wait before retry
            continue
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.2)  # Brief wait before retry
            continue
    # All retries failed - fall back to direct HID
    return None, None


def _recv_exactly(s, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connection closed")
        buf += chunk
    return buf


# ---------- Path 2: Direct HID ----------

def _handle_direct(msg: dict) -> dict:
    """Handle the message directly via fido2 HID, without solokeys-gui."""
    try:
        from solo2.transport import call_device_apdu
        from solo_gui.oath_bridge import OATHBridge, OATHTouchRequired, OATHPINRequired
    except ImportError as e:
        return {"success": False, "error": f"Direct HID not available: {e}"}

    bridge = OATHBridge(transport=call_device_apdu)
    action = msg.get("action")

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


# ---------- Main ----------

def main():
    msg = read_message()
    if msg is None:
        send_message({"success": False, "error": "No message received"})
        return

    # Always prefer GUI socket when available - GUI handles device state properly
    # Only fall back to direct HID if socket doesn't exist or fails
    if _PATH_OVERRIDE != "direct":
        response, fatal_error = _try_gui_socket(msg, retries=2)
        if response is not None:
            send_message(response)
            return
        if fatal_error is not None:
            send_message({"success": False, "error": fatal_error})
            return
        if _PATH_OVERRIDE == "socket":
            send_message({"success": False,
                          "error": "SoloKeys GUI is not running (SOLOKEYS_PATH=socket)"})
            return

    send_message(_handle_direct(msg))


if __name__ == '__main__':
    main()
