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
- Background threads must **never** open HID device handles on Windows (see §8).
  Use `list_presence_ids()` for presence checks, not fido2 enumeration.
- Tab `set_device()` methods must **not** send APDUs. Use a separate `load_data()`
  method triggered after the required applet has been selected (see §8 HmacTab).

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

## 7  Linux packaging model

### Python dependency strategy

The `.deb` and `.rpm` packages do **not** list Python libraries as distro
dependencies (except PySide6, which is too large to bundle and is stable in
Ubuntu 22.04+). Instead, all other Python deps are installed at package
install time via pip into the app's private directory.

| Package | Why handled this way |
|---------|----------------------|
| `PySide6` | ~150 MB; kept as distro dep (`python3-pyside6.*`) |
| `fido2`, `requests`, `pyusb`, `qtawesome` | Pure-Python or abi3 wheels; installed via pip postinst |
| `pyscard`, `hidapi` | Per-Python-version C extensions; installed via pip postinst so the correct ABI wheel is fetched for the user's actual Python |

### `packaging/linux/requirements-bundled.txt`

Single source of truth for the bundled deps and their version lower bounds
(kept in sync with `pyproject.toml`). This file is shipped inside the package
at `/usr/lib/solokeys-gui/requirements-bundled.txt`.

**When you bump a dep version in `pyproject.toml`, also update this file.**

### Install-time pip step

`postinst` (deb) / `%post` (rpm) run:
```sh
python3 -m pip install \
    --target /usr/lib/solokeys-gui/site-packages \
    --requirement /usr/lib/solokeys-gui/requirements-bundled.txt \
    --prefer-binary --no-compile --quiet
```

The wrapper scripts (`/usr/bin/solokeys-gui`, `/usr/bin/solokeys-secrets-host`)
prepend `site-packages` to `PYTHONPATH` so bundled packages take precedence over
any stale system-installed versions.

### Python upgrade trigger

If the user upgrades Python (e.g. 3.12 → 3.13), the C extension wheels need to
be re-fetched for the new ABI.

- **deb**: `packaging/linux/debian/triggers` declares `interest-noawait /usr/bin/python3`.
  dpkg calls `postinst triggered` automatically after any `python3` package update.
- **rpm**: `%triggerin -- python3` scriptlet in `solokeys-gui.spec.in` fires on
  any `python3` install or upgrade.

Both re-run the same pip install command so the correct wheel for the new Python
version is installed. No manual intervention required.

---

## 8  Windows-specific device handling

### USBMonitor platform strategy

`USBMonitor` (in `utils/usb_monitor.py`) runs on **all platforms** but uses a
different backend depending on the OS:

| Platform | Backend | Why |
|----------|---------|-----|
| Linux/macOS | `DeviceWatcher` (fido2 enumeration) | Reads `/dev/hidraw*` without opening devices — lightweight and safe |
| Windows | `list_presence_ids()` (hidapi `hid.enumerate`) | Reads VID/PID/path via SetupAPI without opening a data connection |

**Why not fido2 on Windows?** fido2's HID enumeration **opens device handles**,
which conflicts with the active CTAP2 session held by `DeviceManager`. This caused:

- APDU errors (0x6d00) from interrupted applet selection
- Spurious disconnect/reconnect cycles
- Constant device LED blinking

**`list_presence_ids()`** (`solo2.discovery`) calls `hid.enumerate()` which uses
SetupAPI/`HidD_GetAttributes` to read device info without opening data connections.
It returns device IDs in the same `hid:{path!r}` format as `list_regular_descriptors()`.

**Detection flow on Windows:**
1. `USBMonitor` polls `list_presence_ids()` every 0.5 s in a background thread
2. On connect: emits `device_connected` → `DeviceMonitor._on_usb_device_connected()`
   triggers `_scan_devices()` (full discovery) immediately + delayed retries at 750 ms
   and 1500 ms (Windows can emit USB arrival before the new mode is fully discoverable)
3. On disconnect: emits `device_disconnected` → if the device_id matches a tracked
   device, immediate disconnect; otherwise the 1 s poll timer with grace period handles it
4. The 1 s `QTimer` in `DeviceMonitor` runs as a fallback on all platforms

**Do not replace the Windows backend with fido2 enumeration** unless fido2 adds a
lightweight presence-check mode that does not open HID handles.

### Stale HID handle retry

On Windows the CTAP2 HID handle can go stale (`OSError` / `WinError 1167`) if
the 1 s discovery poll or vault APDU commands interfere with the session.
`_ensure_device()` only checks `self._ctap2 is not None` — it does not verify
the handle is alive.

`_do_set_pin`, `_do_change_pin`, and `_do_browser_apdu` catch `OSError`, reopen
the device via `_reopen_device()`, and retry once. If adding new `_do_*` handlers
to `DeviceManager`, follow the same pattern.

### HmacTab deferred loading

`HmacTab.set_device()` must **not** send APDUs immediately. The OATH applet
needs to be selected first by `VaultTab._check_status()` (which sends SELECT
OATH). `HmacTab` sets up the worker in `set_device()` but waits for
`VaultTab._on_status_checked()` to call `hmac_tab.load_data()`. Sending
INS_LIST before SELECT → 0x6d00 on any platform.

### Common pitfalls (quick reference)

| Symptom | Root cause | Rule |
|---------|-----------|------|
| 0x6d00 APDU errors, device LED blinking | Background thread opens HID handles (fido2 enumeration) while CTAP session is active | Never use `DeviceWatcher`/fido2 HID enum on Windows; use `list_presence_ids()` |
| 0x6d00 on tab load | APDU sent before applet SELECT | Tab `set_device()` must not send APDUs; defer to `load_data()` after applet selection |
| `WinError 1167` / `OSError` on PIN ops | Stale HID handle after concurrent access | `_do_*` handlers must catch `OSError`, call `_reopen_device()`, retry once |
| Device unplug not detected on Windows | USBMonitor disabled or using wrong backend | USBMonitor must run on Windows with `list_presence_ids()` backend |
| Spurious disconnect/reconnect on Windows | Discovery poll transiently fails | Use `_disconnect_grace_scans = 3` on Windows (consecutive misses before disconnect) |
| Device reconnect missed on Windows | USB arrival event fires before new mode is discoverable | `_on_usb_device_connected` must trigger delayed re-scans (750 ms + 1500 ms) |

---

## 9  PR / AI-agent checklist

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

### Windows / cross-platform checks

- [ ] No code opens HID device handles in a background thread (causes CTAP session
      conflicts on Windows). Use `list_presence_ids()` for presence detection.
- [ ] `USBMonitor` changes preserve the platform split: `DeviceWatcher` on
      Linux/macOS, `list_presence_ids()` on Windows. Never use fido2 HID
      enumeration on Windows.
- [ ] Tab `set_device()` does not send APDUs. Applet selection must happen first
      (e.g. VaultTab sends SELECT OATH before HmacTab can send INS_LIST).
- [ ] New `DeviceManager._do_*` handlers catch `OSError` and retry after
      `_reopen_device()` (Windows stale HID handle pattern, see §8).
- [ ] `DeviceMonitor` disconnect detection uses grace period on Windows
      (`_disconnect_grace_scans = 3`) — do not reduce without testing.
- [ ] `_on_usb_device_connected` triggers `_scan_devices()` with delayed retries
      (750 ms, 1500 ms) — Windows USB arrival fires before mode is discoverable.
- [ ] If bumping a dep version in `pyproject.toml`, also update
      `packaging/linux/requirements-bundled.txt` (see §7).
