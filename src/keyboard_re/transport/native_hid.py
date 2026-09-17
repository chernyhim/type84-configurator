"""
Native Windows HID Transport for IO by Red Square Type 84 Magnetic Black.

Communicates over the dedicated configuration interface:
- VID: 0x0C45
- PID: 0x80D6
- Product Name: IO Type 84 Magnetic Black
- Usage Page: 0xFF68
- Usage: 0x0061
- Report ID: 0 (64-byte reports)

Strict Protocol Decoupling:
This transport only knows report IDs and 64-byte payload buffers.
It contains NO protocol opcodes (AA 27, AA 17, Hall, RGB, etc.).
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any, Optional

try:
    import hid
except ImportError:
    hid = None

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size

logger = logging.getLogger(__name__)

TARGET_VID: int = 0x0C45
TARGET_PID: int = 0x80D6
TARGET_USAGE_PAGE: int = 0xFF68
TARGET_USAGE: int = 0x0061
TARGET_PRODUCT_NAME: str = "IO Type 84 Magnetic Black"


class NativeHidTransport:
    """
    Physical USB HID transport using Windows HID API (hidapi).
    Strictly binds only to the verified vendor configuration interface (FF68:0061).
    """

    def __init__(
        self,
        vid: int = TARGET_VID,
        pid: int = TARGET_PID,
        usage_page: int = TARGET_USAGE_PAGE,
        usage: int = TARGET_USAGE,
        product_name: str = TARGET_PRODUCT_NAME,
    ) -> None:
        self.vid = vid
        self.pid = pid
        self.usage_page = usage_page
        self.usage = usage
        self.product_name = product_name
        self._dev: Optional[Any] = None
        self._device_path: Optional[bytes] = None
        self._actual_product_name: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        """Return True if device is currently opened."""
        return self._dev is not None

    @property
    def device_path(self) -> Optional[bytes]:
        """Return the device interface path."""
        return self._device_path

    @property
    def actual_product_name(self) -> Optional[str]:
        """Return the actual product name read from device descriptor."""
        return self._actual_product_name

    def find_target_interface(self) -> bytes:
        """
        Enumerate connected HID devices and locate the exact configuration interface.
        Raises RuntimeError if not found or product string does not match.
        """
        if hid is None:
            raise RuntimeError(
                "The 'hidapi' library is not installed. Install it with: pip install hidapi"
            )

        devices = hid.enumerate(self.vid, self.pid)
        for dev_info in devices:
            dev_up = dev_info.get("usage_page")
            dev_u = dev_info.get("usage")
            prod = dev_info.get("product_string") or ""

            if dev_up == self.usage_page and dev_u == self.usage:
                if self.product_name and self.product_name.lower() not in prod.lower():
                    logger.warning(
                        f"Found matching VID/PID/Usage but product name mismatch: '{prod}' vs '{self.product_name}'"
                    )
                    continue

                self._actual_product_name = prod
                path = dev_info.get("path")
                if isinstance(path, str):
                    path = path.encode("utf-8")
                return path

        raise RuntimeError(
            f"Target HID interface not found (VID=0x{self.vid:04X}, PID=0x{self.pid:04X}, "
            f"UsagePage=0x{self.usage_page:04X}, Usage=0x{self.usage:04X}, Product='{self.product_name}')"
        )

    def open(self) -> None:
        """Open the HID device configuration interface."""
        if self._dev is not None:
            return

        if hid is None:
            raise RuntimeError(
                "The 'hidapi' library is not installed. Install it with: pip install hidapi"
            )

        path = self.find_target_interface()
        dev = hid.device()
        try:
            dev.open_path(path)
        except Exception as e:
            raise RuntimeError(f"Failed to open HID device at path {path!r}: {e}") from e

        self._dev = dev
        self._device_path = path
        logger.debug(f"Opened HID device at {path!r}")

    def close(self) -> None:
        """Close the HID device handle."""
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception as e:
                logger.warning(f"Error while closing HID device: {e}")
            finally:
                self._dev = None
                self._device_path = None
                logger.debug("Closed HID device")

    def __enter__(self) -> NativeHidTransport:
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def write_report(self, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> None:
        """
        Write a 64-byte output report to the device.
        On Windows hidapi, write() expects [report_id, byte0, byte1, ..., byte63].
        """
        if not self.is_connected or self._dev is None:
            raise ConnectionError("HID device is not open")

        validate_report_size(data, REPORT_SIZE)
        payload = bytes([report_id & 0xFF]) + bytes(data)

        # On Windows hidapi, write() takes the report_id byte + report data
        written = self._dev.write(payload)
        if written < 0:
            raise IOError(f"HID write failed on device {self._device_path!r}")
        if written != len(payload):
            raise IOError(
                f"HID short write: expected {len(payload)} bytes, sent {written} bytes"
            )

    def read_report(self, timeout_ms: int = 1000) -> Optional[bytes]:
        """
        Read a 64-byte input report from the device.
        Returns None if timeout expires before data is received.
        """
        if not self.is_connected or self._dev is None:
            raise ConnectionError("HID device is not open")

        # dev.read returns a list of integers or empty list on timeout
        resp = self._dev.read(REPORT_SIZE, timeout_ms)
        if not resp:
            return None

        raw = bytes(resp)
        if len(raw) != REPORT_SIZE:
            raise IOError(
                f"Unexpected HID report length: expected {REPORT_SIZE} bytes, got {len(raw)}"
            )
        return raw

    def drain_input_buffer(self, max_reports: int = 512) -> int:
        """
        Drain unread/stale reports waiting in the OS HID input buffer.
        Uses non-blocking read (timeout_ms=0).
        Returns the number of discarded reports.
        """
        if not self.is_connected or self._dev is None:
            return 0

        count = 0
        while count < max_reports:
            # Note: in python-hidapi, timeout_ms=0 triggers blocking hid_read().
            # Using timeout_ms=1 invokes hid_read_timeout(1ms) which drains immediate OS buffer non-blockingly.
            resp = self._dev.read(REPORT_SIZE, 1)
            if not resp:
                break
            count += 1
            logger.debug(
                f"Drained stale HID report #{count} ({len(resp)}B): "
                f"{bytes(resp)[:8].hex(' ').upper()}"
            )

        if count > 0:
            logger.info(f"Drained {count} stale HID report(s) from input buffer")
        return count

    # Protocol-layer compatibility methods (matches HidTransport Protocol)
    def send_report(self, report_id: int, data: bytes) -> None:
        """Send a 64-byte report to the device (HOST -> DEVICE)."""
        self.write_report(data, report_id=report_id)

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: Optional[int] = None,
        expected_address: Optional[int] = None,
    ) -> bytes:
        """
        Receive a 64-byte report from the device (DEVICE -> HOST).
        If expected_opcode / expected_address are provided:
        Ignores stale reports from preceding operations within the total timeout deadline.
        NEVER accepts a mismatched opcode as a valid ACK.
        Raises TimeoutError if deadline expires without receiving a matching report.
        """
        deadline = time.monotonic() + timeout
        while True:
            time_left = max(0.001, deadline - time.monotonic())
            timeout_ms = max(1, int(time_left * 1000))
            resp = self.read_report(timeout_ms=timeout_ms)
            if resp is None:
                if expected_opcode is not None:
                    raise TimeoutError(
                        f"HID receive timed out after {timeout:.2f}s waiting for opcode 0x{expected_opcode:02X}"
                    )
                raise TimeoutError(f"HID receive timed out after {timeout:.2f}s")

            # If no filtering requested, return immediately
            if expected_opcode is None:
                return resp

            # Verify if this report is an ACK (starts with 0x55)
            if resp[0] == 0x55:
                opcode = resp[1]
                addr = struct.unpack_from("<H", resp, 3)[0] if len(resp) >= 5 else None

                opcode_matches = (opcode == expected_opcode)
                addr_matches = (expected_address is None or addr == expected_address)

                if opcode_matches and addr_matches:
                    return resp

                addr_str = f"0x{addr:04X}" if addr is not None else "N/A"
                exp_addr_str = f"0x{expected_address:04X}" if expected_address is not None else "ANY"
                logger.warning(
                    f"Discarding stale HID report (opcode 0x{opcode:02X}, addr {addr_str}), "
                    f"awaiting opcode 0x{expected_opcode:02X}, addr {exp_addr_str} "
                    f"({deadline - time.monotonic():.3f}s left)"
                )
                continue

            # Non-ACK packet received when expecting ACK
            logger.warning(
                f"Discarding non-ACK HID report (prefix 0x{resp[0]:02X}) while awaiting opcode 0x{expected_opcode:02X}"
            )
            continue
