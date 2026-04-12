# solokeys-gui — Architecture Guide

This document is for human contributors and AI coding agents. It defines the
layer boundaries, threading model, and coding rules that all contributions must
follow. Violations are catalogued at the end.

---

## 1  Two-repo architecture

| Repo | Role |
|------|------|
| `solo2-python` (`src/solo2/`) | Hardware abstraction library. Owns all transport code (USB HID, PC/SC, USB bootloader). The only place that imports `pyusb`, `pyscard`, `fido2`, or any other transport-level library. |
| `solokeys-gui` (`src/solo_gui/`) | GUI application. Imports `solo2` as a library. Must **not** import transport libraries directly. |

---

## 2  Layer model

Dependency arrows point downward only. Upper layers must never reach past the
layer directly below them.

```
┌─────────────────────────────────────────────┐
│  Views (tabs/, main_window.py)              │  Layer 5 — UI only
│  Reads cached state. Never does I/O.        │
├─────────────────────────────────────────────┤
│  Workers (workers/)                         │  Layer 4 — background I/O
│  QThread helpers. Call solo2 APIs.          │
├─────────────────────────────────────────────┤
│  DeviceManager (device_manager.py)          │  Layer 3 — session state
│  Long-lived CTAP2 session, PIN-token cache. │  Only fido2 exception lives here.
├─────────────────────────────────────────────┤
│  Models (models/)                           │  Layer 2 — domain objects
│  Solo2Device, DeviceMonitor, DeviceInfo.    │  Thin wrappers over solo2.
├─────────────────────────────────────────────┤
│  solo2 library (separate repo)              │  Layer 1 — transport
│  USB HID, PC/SC, bootloader, fido2, pyusb.  │
└─────────────────────────────────────────────┘
```

---

## 3  Transport layers (owned by solo2-python)

All transport I/O belongs in `solo2-python`. The GUI calls into these APIs:

| Transport | solo2 module | GUI entry point |
|-----------|-------------|-----------------|
| USB HID (CTAP2/FIDO2) | `solo2.hid_backend` | `Solo2Device.open_hid_device()` |
| PC/SC (CCID applets) | `solo2.pcsc` | `solo2.pcsc.iter_pcsc_connections()` |
| USB bootloader | `solo2.discovery` | `list_bootloader_descriptors()` |
| Device discovery | `solo2.discovery` | `DeviceWatcher`, `list_regular_descriptors()` |

### PC/SC connection API

Workers that talk to CCID applets (PIV, OpenPGP, OATH) must use:

```python
from solo2.pcsc import PCSC_AVAILABLE, PCSC_IMPORT_ERROR, iter_pcsc_connections

for connection in iter_pcsc_connections():
    response, sw1, sw2 = connection.transmit(apdu_list)
    # ... handle response ...
    connection.close()
```

`iter_pcsc_connections()` handles protocol selection (T=1 preferred, then auto)
internally. Workers must not import `smartcard`, `pyscard`, or any other
PC/SC library directly.

### Device hot-plug

Use `solo2.discovery.DeviceWatcher` (via `utils/usb_monitor.py`) or the
`DeviceMonitor` poll timer. Never call `usb.core.find()` from the GUI layer.

---

## 4  Threading model

```
Main thread (Qt event loop)
  ├── DeviceManager         runs on main thread
  │     Owns CTAP2 session, PIN-token cache, credential list.
  │     Called by workers via direct method calls (thread-safe by design).
  │
  ├── DeviceMonitor         runs on main thread
  │     QTimer-based poller, calls solo2.discovery APIs every 1 s.
  │     USBMonitor (background thread) triggers faster scans.
  │
  └── QThread workers       background threads
        PivWorker, GpgWorker, AdminWorker, FirmwareWorker, …
        Each creates a temporary PC/SC or HID connection for its task.
        Communicate back to the UI via Qt signals only.
```

**Rules:**
- Workers must never touch Qt widgets directly.
- Workers must never access `DeviceManager._ctap2` or internal CTAP2 state.
- Only `DeviceManager` may call `DeviceManager.get_pin_retries()`,
  `set_pin()`, `change_pin()`, `get_credentials()` etc.
- Views read only cached/already-computed data (no blocking I/O on main thread).

---

## 5  Coding rules

### What each layer MAY import

| Layer | Allowed imports |
|-------|----------------|
| Views | `PySide6`, `solo_gui.models`, `solo_gui.device_manager` |
| Workers | `PySide6.QtCore`, `solo2.*`, `solo_gui.models` |
| DeviceManager | `PySide6.QtCore`, `solo2.*`, **`fido2.*`** (documented exception) |
| Models | `PySide6.QtCore`, `solo2.*` |

### What is FORBIDDEN in the GUI layer (`src/solo_gui/`)

| Library | Reason |
|---------|--------|
| `usb`, `usb.core` (pyusb) | Transport owned by `solo2.discovery` |
| `smartcard.*` (pyscard) | Transport owned by `solo2.pcsc` |
| `fido2.*` anywhere except `device_manager.py` | State owned by DeviceManager |

### Shim files — correct by design

These files re-export solo2 types and are **not** violations:

| File | What it does |
|------|-------------|
| `models/device.py` | Re-exports `Solo2Device`, `SoloDevice`, `DeviceInfo` |
| `hid_backend.py` | Re-exports `solo2.hid_backend` |
| `device_transport.py` | Re-exports `solo2` transport helpers |

---

## 6  Violation map

### Fixed

| File | Violation | Severity | Status |
|------|-----------|----------|--------|
| `utils/helpers.py` | `from fido2.ctap2 import PinProtocolV1` — dead `verify_pin_with_retry()` | Low | **Fixed** (function deleted) |
| `utils/usb_monitor.py` | `import usb.core` — duplicate discovery path | Medium | **Fixed** (replaced with `DeviceWatcher`) |
| `workers/piv_worker.py` | `from smartcard.System import readers` | High | **Fixed** (replaced with `iter_pcsc_connections()`) |
| `workers/gpg_worker.py` | `from smartcard.System import readers` | High | **Fixed** (replaced with `iter_pcsc_connections()`) |

### Documented exception (not a bug)

| File | Import | Reason |
|------|--------|--------|
| `device_manager.py` | `from fido2.ctap2 import Ctap2`, `CredentialManagement`, `ClientPin`, `CTAPHID` | DeviceManager is the sole owner of the long-lived CTAP2 session with PIN-token caching. This cannot be delegated to a stateless solo2 wrapper. Must **never** migrate to workers, tabs, helpers, or views. |

### Open violations (out of scope for initial cleanup)

| File | Violation | Severity | Fix |
|------|-----------|----------|-----|
| `workers/fido2_worker_simple.py` | `from fido2.ctap2.base import Ctap2, Info` | Medium | Migrate CTAP2 calls into DeviceManager methods; this worker should call `device_manager.make_credential()` etc. |
| `workers/firmware_worker.py` | `from fido2.ctap2 import Ctap2` (inside a function) | Low | Move into DeviceManager or solo2 helper |

---

## 7  PR / AI-agent checklist

Before merging any change to `src/solo_gui/`, verify:

```bash
# Must be empty (only device_manager.py may import fido2):
grep -r "from fido2" src/solo_gui/ | grep -v device_manager.py

# Must be empty (pyusb belongs in solo2-python):
grep -r "import usb" src/solo_gui/

# Must be empty (pyscard belongs in solo2-python):
grep -r "from smartcard" src/solo_gui/

# Workers must use solo2.pcsc, not raw pyscard:
grep -r "readers()\|createConnection\|CardConnection" src/solo_gui/workers/
```

Additional checks:
- [ ] New workers import `solo2.*`, not transport libs directly.
- [ ] New UI code (tabs, views) only reads cached `DeviceInfo` — no blocking calls.
- [ ] PIN operations go through `DeviceManager`, not raw `ClientPin`.
- [ ] Device discovery goes through `DeviceMonitor` or `solo2.discovery`, not pyusb.
- [ ] PC/SC connections use `iter_pcsc_connections()` from `solo2.pcsc`.
