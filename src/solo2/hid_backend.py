"""CTAP HID backend helpers for Solo 2 devices."""

from __future__ import annotations

import logging
import sys
from typing import Iterator

from fido2.hid import CtapHidDevice

_log = logging.getLogger("solo2device")


def _list_windows_fido2_devices() -> Iterator[CtapHidDevice]:
    """Enumerate Windows CTAP HID devices via fido2's native SetupAPI backend."""
    from fido2.hid import windows as backend

    descriptors = backend.list_descriptors()
    _log.debug("fido2.windows list_descriptors returned %d device(s)", len(descriptors))

    for descriptor in descriptors:
        _log.debug(
            "fido2.windows descriptor path=%r vid=0x%04x pid=0x%04x in=%s out=%s "
            "product=%r serial=%r",
            descriptor.path,
            descriptor.vid,
            descriptor.pid,
            descriptor.report_size_in,
            descriptor.report_size_out,
            descriptor.product_name,
            descriptor.serial_number,
        )
        try:
            yield CtapHidDevice(descriptor, backend.open_connection(descriptor))
        except Exception as exc:
            _log.exception(
                "fido2.windows failed to open CTAP device path=%r: %s",
                descriptor.path,
                exc,
            )


def list_ctap_hid_devices() -> Iterator[CtapHidDevice]:
    """List CTAP HID devices, preferring the native Windows fido2 backend."""
    if sys.platform == "win32":
        try:
            _log.debug("list_ctap_hid_devices using native fido2 Windows backend")
            yield from _list_windows_fido2_devices()
            return
        except Exception as exc:
            _log.exception(
                "native fido2 Windows backend failed, falling back to generic backend: %s",
                exc,
            )

    _log.debug("list_ctap_hid_devices using generic fido2 backend")
    yield from CtapHidDevice.list_devices()
