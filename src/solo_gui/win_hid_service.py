"""Windows service: SoloKeys HID Proxy.

Runs as LocalSystem so it can open the HID device on Windows 10 1903+.
Non-admin GUI processes connect via the named pipe and forward CTAP2 calls.

Install / start:
    python win_hid_service.py install
    python win_hid_service.py start

Or via sc.exe (done by installer):
    sc create SoloKeysHID binPath="solokeys-service.exe" start=auto
    sc start  SoloKeysHID
"""

import sys
import threading
import time
import logging

_log = logging.getLogger("solokeys-service")

PIPE_NAME = r"\\.\pipe\solokeys-hid"
SOLOKEYS_VID = 0x1209
SOLOKEYS_PID = 0xBEEE


def _handle_connection(conn, get_hid_dev, hid_lock):
    """Serve one pipe client in a daemon thread."""
    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                break

            msg_type = msg.get("type")

            if msg_type == "status":
                hid_dev = get_hid_dev()
                if hid_dev is None:
                    conn.send({"type": "result", "device_present": False, "capabilities": 0})
                else:
                    try:
                        with hid_lock:
                            hid_dev.call(0x61, b'')
                        conn.send({
                            "type": "result",
                            "device_present": True,
                            "capabilities": getattr(hid_dev, "_capabilities", 0),
                        })
                    except Exception:
                        conn.send({"type": "result", "device_present": False, "capabilities": 0})
                break  # status is short-lived; close after one exchange

            elif msg_type == "call":
                hid_dev = get_hid_dev()
                if hid_dev is None:
                    conn.send({"type": "error", "message": "device not present"})
                    continue
                try:
                    cmd = msg["cmd"]
                    data = bytes.fromhex(msg.get("data", ""))
                    keepalives = []

                    def _on_keepalive(status):
                        keepalives.append(status)
                        conn.send({"type": "keepalive", "status": status})

                    with hid_lock:
                        result = hid_dev.call(cmd, data, on_keepalive=_on_keepalive)
                    conn.send({"type": "result", "data": result.hex()})
                except Exception as e:
                    # Try to extract CtapError code
                    try:
                        from fido2.ctap import CtapError
                        if isinstance(e, CtapError):
                            conn.send({"type": "error", "ctap_code": e.code.value})
                            continue
                    except ImportError:
                        pass
                    conn.send({"type": "error", "message": str(e)})

            elif msg_type == "wink":
                hid_dev = get_hid_dev()
                if hid_dev is None:
                    conn.send({"type": "error", "message": "device not present"})
                    continue
                try:
                    with hid_lock:
                        hid_dev.wink()
                    conn.send({"type": "result"})
                except Exception as e:
                    conn.send({"type": "error", "message": str(e)})

            else:
                conn.send({"type": "error", "message": f"unknown message type: {msg_type!r}"})
    except Exception as e:
        _log.error("_handle_connection: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _device_keeper(hid_dev_holder, hid_lock, stop_event):
    """Background thread: keeps hid_dev_holder[0] pointing at a live device."""
    from fido2.hid import CtapHidDevice
    while not stop_event.is_set():
        if hid_dev_holder[0] is None:
            try:
                for dev in CtapHidDevice.list_devices():
                    desc = getattr(dev, "descriptor", None)
                    if not desc:
                        continue
                    if getattr(desc, "vid", None) == SOLOKEYS_VID and \
                       getattr(desc, "pid", None) == SOLOKEYS_PID:
                        with hid_lock:
                            hid_dev_holder[0] = dev
                        _log.info("HID device opened: %r", getattr(desc, "path", None))
                        break
            except Exception as e:
                _log.debug("device scan: %s", e)
        stop_event.wait(1.0)


def run_service_loop(stop_event):
    """Main service body: open pipe, accept connections."""
    from multiprocessing.connection import Listener

    hid_dev_holder = [None]
    hid_lock = threading.Lock()

    keeper = threading.Thread(
        target=_device_keeper,
        args=(hid_dev_holder, hid_lock, stop_event),
        daemon=True,
    )
    keeper.start()

    def get_hid_dev():
        return hid_dev_holder[0]

    try:
        listener = Listener(PIPE_NAME, family="AF_PIPE")
    except Exception as e:
        _log.error("Could not open pipe %s: %s", PIPE_NAME, e)
        return

    _log.info("Listening on %s", PIPE_NAME)

    while not stop_event.is_set():
        listener._listener._socket.settimeout(1.0)
        try:
            conn = listener.accept()
        except Exception:
            continue
        t = threading.Thread(
            target=_handle_connection,
            args=(conn, get_hid_dev, hid_lock),
            daemon=True,
        )
        t.start()

    listener.close()
    _log.info("Service loop exiting")


# ---------------------------------------------------------------------------
# Windows service wrapper (pywin32)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager

        class SoloKeysHIDService(win32serviceutil.ServiceFramework):
            _svc_name_ = "SoloKeysHID"
            _svc_display_name_ = "SoloKeys HID Proxy"
            _svc_description_ = (
                "Provides non-admin processes access to the SoloKeys HID device."
            )

            def __init__(self, args):
                win32serviceutil.ServiceFramework.__init__(self, args)
                self._stop_event = win32event.CreateEvent(None, 0, 0, None)
                self._py_stop = threading.Event()

            def SvcStop(self):
                self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                win32event.SetEvent(self._stop_event)
                self._py_stop.set()

            def SvcDoRun(self):
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_INFORMATION_TYPE,
                    servicemanager.PYS_SERVICE_STARTED,
                    (self._svc_name_, ""),
                )
                run_service_loop(self._py_stop)

    except ImportError:
        SoloKeysHIDService = None  # type: ignore


if __name__ == "__main__":
    if sys.platform == "win32" and SoloKeysHIDService is not None:
        win32serviceutil.HandleCommandLine(SoloKeysHIDService)
    else:
        # Fallback: run as a plain process (useful for testing on non-Windows)
        stop = threading.Event()
        try:
            run_service_loop(stop)
        except KeyboardInterrupt:
            stop.set()
