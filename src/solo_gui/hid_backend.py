"""CTAP HID device helpers with a hidapi-backed Windows implementation."""

from __future__ import annotations

import sys
from typing import Iterator

from fido2.hid import CtapHidDevice
from fido2.hid.base import CtapHidConnection, FIDO_USAGE, FIDO_USAGE_PAGE, HidDescriptor


class HidApiCtapHidConnection(CtapHidConnection):
    """hidapi-backed CTAP HID connection for Windows."""

    def __init__(self, descriptor: HidDescriptor):
        import hid

        self.descriptor = descriptor
        self._handle = hid.device()
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

    seen_paths = set()
    for info in hid.enumerate():
        usage_page = info.get("usage_page")
        usage = info.get("usage")
        path = info.get("path")
        if usage_page != FIDO_USAGE_PAGE or usage != FIDO_USAGE or not path:
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)

        descriptor = HidDescriptor(
            path=path,
            vid=info.get("vendor_id") or 0,
            pid=info.get("product_id") or 0,
            report_size_in=info.get("input_report_length") or 64,
            report_size_out=info.get("output_report_length") or 64,
            product_name=info.get("product_string"),
            serial_number=info.get("serial_number"),
        )
        yield CtapHidDevice(descriptor, HidApiCtapHidConnection(descriptor))


def list_ctap_hid_devices() -> Iterator[CtapHidDevice]:
    """List CTAP HID devices, preferring hidapi on Windows."""
    if sys.platform == "win32":
        try:
            yield from _list_windows_hidapi_devices()
            return
        except Exception:
            # Fall back to the library's native backend if hidapi is unavailable.
            pass
    yield from CtapHidDevice.list_devices()
