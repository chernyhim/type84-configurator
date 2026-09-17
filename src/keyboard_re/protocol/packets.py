"""
Core packet definitions and helpers for IO by Red Square Type 84 Magnetic Black.

Offline protocol layer: defines wire constants, report dimensions, and padding helpers.
NO HID transmission or hardware write operations.
"""

from __future__ import annotations

# Hardware and USB interface constants
EXPECTED_VID: int = 0x0C45
EXPECTED_PID: int = 0x80D6
USAGE_PAGE: int = 0xFF68
USAGE: int = 0x0061

# HID Report configuration
REPORT_ID: int = 0
REPORT_SIZE: int = 64


def pad_report(payload: bytes | bytearray, target_size: int = REPORT_SIZE) -> bytes:
    """
    Pad a report payload with trailing zeroes to the fixed target size (64 bytes).
    Raises ValueError if payload already exceeds target_size.
    """
    if len(payload) > target_size:
        raise ValueError(
            f"Payload length {len(payload)} exceeds target report size {target_size}"
        )
    return bytes(payload) + b"\x00" * (target_size - len(payload))


def validate_report_size(report: bytes | bytearray, expected_size: int = REPORT_SIZE) -> None:
    """
    Validate that a raw report matches the expected length.
    """
    if len(report) != expected_size:
        raise ValueError(
            f"Invalid report size: expected {expected_size} bytes, got {len(report)}"
        )
