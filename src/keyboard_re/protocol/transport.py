"""
Abstract HID Transport Layer and Mock/DryRun Transports for Type 84.

Strict Safety Policy:
- NO direct hardware I/O or USB transmissions.
- Production Python code runs through MockHidTransport or DryRunTransport.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Protocol, Tuple

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size


class HidTransport(Protocol):
    """
    Abstract interface for USB HID communication.
    """
    def open(self) -> None:
        """Open the HID device connection."""
        ...

    def close(self) -> None:
        """Close the HID device connection."""
        ...

    def send_report(self, report_id: int, data: bytes) -> None:
        """Send a 64-byte report to the device (HOST -> DEVICE)."""
        ...

    def drain_input_buffer(self, max_reports: int = 64) -> int:
        """Drain any stale input reports."""
        ...

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: Optional[int] = None,
        expected_address: Optional[int] = None,
    ) -> bytes:
        """Receive a 64-byte report from the device (DEVICE -> HOST)."""
        ...

    def write_report(self, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> None:
        """Write a 64-byte report to the device."""
        ...

    def read_report(self, timeout_ms: int = 1000) -> Optional[bytes]:
        """Read a 64-byte report from the device."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if device is connected and open."""
        ...


class MockHidTransport:
    """
    In-memory Mock HID Transport for automated testing and simulation.
    
    Features:
    - Records all sent packets.
    - Automatic ACK generation (e.g. AA 27 38 <addr> -> 55 27 38 <addr>).
    - Queue of manual responses.
    - Error simulation (timeout, invalid ACK opcode, invalid address).
    """
    def __init__(
        self,
        auto_ack: bool = True,
        simulate_timeout: bool = False,
        simulate_wrong_ack_opcode: Optional[int] = None,
        simulate_wrong_ack_address: Optional[int] = None,
        fail_next_write: bool = False,
    ):
        self.auto_ack: bool = auto_ack
        self.simulate_timeout: bool = simulate_timeout
        self.simulate_wrong_ack_opcode: Optional[int] = simulate_wrong_ack_opcode
        self.simulate_wrong_ack_address: Optional[int] = simulate_wrong_ack_address
        self.fail_next_write: bool = fail_next_write

        self.recorded_reports: List[Tuple[int, bytes]] = []
        self.sent_packets = self.recorded_reports
        self.response_queue: List[bytes] = []
        self._is_open: bool = True

    @property
    def is_connected(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def queue_response(self, report: bytes) -> None:
        """Manually queue a 64-byte response packet to be returned by receive_report."""
        validate_report_size(report, REPORT_SIZE)
        self.response_queue.append(bytes(report))

    def send_report(self, report_id: int, data: bytes) -> None:
        if not self._is_open:
            raise ConnectionError("Mock HID device is closed")

        if self.fail_next_write:
            self.fail_next_write = False
            raise IOError("Simulated HID write error")

        validate_report_size(data, REPORT_SIZE)
        self.recorded_reports.append((report_id, bytes(data)))

        if self.auto_ack:
            # Generate default ACK: 55 <cmd> <size> <addr_le>
            ack = bytearray(64)
            ack[0] = 0x55
            ack[1] = self.simulate_wrong_ack_opcode if self.simulate_wrong_ack_opcode is not None else data[1]
            ack[2] = data[2]
            if self.simulate_wrong_ack_address is not None:
                struct.pack_into("<H", ack, 3, self.simulate_wrong_ack_address)
            else:
                ack[3:5] = data[3:5]
            ack[5:8] = data[5:8]
            sz = data[2]
            if 0 < sz <= (REPORT_SIZE - 8) and len(data) >= 8 + sz:
                ack[8 : 8 + sz] = data[8 : 8 + sz]
            self.response_queue.append(bytes(ack))

    def drain_input_buffer(self, max_reports: int = 64) -> int:
        """Drain stale input queue in mock transport."""
        if hasattr(self, "stale_queue") and self.stale_queue:
            count = len(self.stale_queue)
            self.stale_queue.clear()
            return count
        return 0

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: Optional[int] = None,
        expected_address: Optional[int] = None,
    ) -> bytes:
        if not self._is_open:
            raise ConnectionError("Mock HID device is closed")

        if self.simulate_timeout:
            raise TimeoutError("Simulated HID transport timeout waiting for response")

        if not self.response_queue:
            raise TimeoutError("Mock HID response queue empty")

        if expected_opcode is None:
            return self.response_queue.pop(0)

        # Check if any report in queue matches expected_opcode and expected_address
        match_idx = -1
        for idx, rep in enumerate(self.response_queue):
            if rep[0] == 0x55:
                opcode = rep[1]
                addr = struct.unpack_from("<H", rep, 3)[0] if len(rep) >= 5 else None
                opcode_matches = (opcode == expected_opcode)
                addr_matches = (expected_address is None or addr == expected_address)
                if opcode_matches and addr_matches:
                    match_idx = idx
                    break

        if match_idx >= 0:
            # Discard preceding stale reports up to match_idx
            for _ in range(match_idx):
                self.response_queue.pop(0)
            return self.response_queue.pop(0)

        # No matching report in queue; return head report so validator can inspect/fail
        return self.response_queue.pop(0)

    def write_report(self, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> None:
        self.send_report(report_id, data)

    def read_report(self, timeout_ms: int = 1000) -> Optional[bytes]:
        try:
            return self.receive_report(timeout=timeout_ms / 1000.0)
        except TimeoutError:
            return None


class DryRunTransport:
    """
    Safety Transport that GUARANTEES zero hardware side-effects.
    
    Used by CLI and testing to generate write plans without touching hardware.
    """
    def __init__(self):
        self.is_dry_run: bool = True
        self.recorded_reports: List[Tuple[int, bytes]] = []
        self._is_open: bool = True

    @property
    def is_connected(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def send_report(self, report_id: int, data: bytes) -> None:
        validate_report_size(data, REPORT_SIZE)
        self.recorded_reports.append((report_id, bytes(data)))

    def drain_input_buffer(self, max_reports: int = 64) -> int:
        return 0

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: Optional[int] = None,
        expected_address: Optional[int] = None,
    ) -> bytes:
        if not self.recorded_reports:
            raise TimeoutError("DryRun transport has no pending simulated responses")

        # Synthesize theoretical ACK: 55 <cmd> <size> <addr_le> [subheader] [payload]
        last_id, last_packet = self.recorded_reports[-1]
        ack = bytearray(64)
        ack[0] = 0x55
        ack[1] = last_packet[1]
        ack[2] = last_packet[2]
        ack[3:5] = last_packet[3:5]
        ack[5:8] = last_packet[5:8]
        sz = last_packet[2]
        if 0 < sz <= (REPORT_SIZE - 8) and len(last_packet) >= 8 + sz:
            ack[8 : 8 + sz] = last_packet[8 : 8 + sz]
        return bytes(ack)

    def write_report(self, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> None:
        self.send_report(report_id, data)

    def read_report(self, timeout_ms: int = 1000) -> Optional[bytes]:
        try:
            return self.receive_report(timeout=timeout_ms / 1000.0)
        except TimeoutError:
            return None
