"""CTAP HID device helpers with a hidapi-backed Windows implementation."""

from __future__ import annotations

import logging
import sys
from typing import Iterator

from fido2.hid import CtapHidDevice
from fido2.hid.base import CtapHidConnection, FIDO_USAGE, FIDO_USAGE_PAGE, HidDescriptor

_log = logging.getLogger("solo2device")


class HidApiCtapHidConnection(CtapHidConnection):
    """hidapi-backed CTAP HID connection for Windows."""

    def __init__(self, descriptor: HidDescriptor):
        import hid

        self.descriptor = descriptor
        self._handle = hid.device()
        _log.debug(
            "hidapi open_path path=%r vid=0x%04x pid=0x%04x in=%s out=%s",
            descriptor.path,
            descriptor.vid,
            descriptor.pid,
            descriptor.report_size_in,
            descriptor.report_size_out,
        )
        self._handle.open_path(descriptor.path)
        self._handle.set_nonblocking(False)

    def close(self) -> None:
        self._handle.close()

    def write_packet(self, data: bytes) -> None:
        out = b"\0" + data
        written = self._handle.write(out)
        if written != len(out):
            raise OSError(
                f"failed to write entire packet: expected {len(out)}, got {written}"
            )

    def read_packet(self) -> bytes:
        data = self._handle.read(self.descriptor.report_size_in + 1)
        if not data:
            raise OSError("failed to read HID packet")
        packet = bytes(data)
        if len(packet) == self.descriptor.report_size_in + 1:
            return packet[1:]
        if len(packet) == self.descriptor.report_size_in:
            return packet
        raise OSError(
            "failed to read full length report from device: "
            f"expected {self.descriptor.report_size_in} or "
            f"{self.descriptor.report_size_in + 1}, got {len(packet)}"
        )


def _list_windows_hidapi_devices() -> Iterator[CtapHidDevice]:
    import hid

    all_devices = hid.enumerate()
    _log.debug("hidapi enumerate returned %d device(s)", len(all_devices))
    seen_paths = set()
    for info in all_devices:
        usage_page = info.get("usage_page")
        usage = info.get("usage")
        path = info.get("path")
        vendor_id = info.get("vendor_id") or 0
        product_id = info.get("product_id") or 0
        product = info.get("product_string")
        serial = info.get("serial_number")
        interface_number = info.get("interface_number")
        _log.debug(
            "hidapi device path=%r vid=0x%04x pid=0x%04x usage_page=%r usage=%r "
            "iface=%r product=%r serial=%r",
            path,
            vendor_id,
            product_id,
            usage_page,
            usage,
            interface_number,
            product,
            serial,
        )
        if usage_page != FIDO_USAGE_PAGE or usage != FIDO_USAGE or not path:
            _log.debug("hidapi skip path=%r reason=usage/path mismatch", path)
            continue
        if path in seen_paths:
            _log.debug("hidapi skip path=%r reason=duplicate", path)
            continue
        seen_paths.add(path)

        descriptor = HidDescriptor(
            path=path,
            vid=vendor_id,
            pid=product_id,
            report_size_in=info.get("input_report_length") or 64,
            report_size_out=info.get("output_report_length") or 64,
            product_name=product,
            serial_number=serial,
        )
        try:
            yield CtapHidDevice(descriptor, HidApiCtapHidConnection(descriptor))
        except Exception as e:
            _log.exception("hidapi failed to open CTAP device path=%r: %s", path, e)


def list_ctap_hid_devices() -> Iterator[CtapHidDevice]:
    """List CTAP HID devices, preferring hidapi on Windows."""
    if sys.platform == "win32":
        try:
            _log.debug("list_ctap_hid_devices using hidapi backend on Windows")
            yield from _list_windows_hidapi_devices()
            return
        except Exception as e:
            # Fall back to the library's native backend if hidapi is unavailable.
            _log.exception("hidapi backend failed, falling back to fido2 backend: %s", e)
    _log.debug("list_ctap_hid_devices using fido2 backend")
    yield from CtapHidDevice.list_devices()
