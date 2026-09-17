"""
Synthetic test fixtures generator representing official web-configurator captures.
Matches the real hardware structure: 84 active keys, offset +5 in 8-byte slots.
"""

from __future__ import annotations

import struct
from keyboard_re.models import (
    ACTUATION_OFFSET,
    CHUNK_COUNT,
    CHUNK_PAYLOAD_SIZE,
    CONFIG_IMAGE_SIZE,
    EXPECTED_PID,
    EXPECTED_VID,
    PREFIX_DATA,
    PREFIX_TERMINATOR,
    RawCapture,
    RawReport,
)

# 84 active slots experimentally extracted from the device baseline
ACTIVE_SLOTS_84 = [
    # Bank 0: 13 keys (cols 0..12)
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    # Bank 1: 13 keys (cols 0..12)
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28,
    # Bank 2: 13 keys (cols 0..12)
    32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44,
    # Bank 3: 13 keys (cols 0..12, includes CapsLock, A, S, D...)
    48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60,
    # Bank 4: 13 keys (cols 0..12)
    64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76,
    # Bank 5: 12 keys (cols 0..5, 7..12)
    80, 81, 82, 83, 84, 85, 87, 88, 89, 90, 91, 92,
    # Bank 6: 7 keys (cols 3, 7..12)
    99, 103, 104, 105, 106, 107, 108
]


def create_mock_report_chunk(addr: int, payload_bytes: bytes, index: int) -> RawReport:
    """Create a 64-byte DATA_CHUNK report starting with AA 27 38."""
    assert len(payload_bytes) == CHUNK_PAYLOAD_SIZE
    buf = bytearray(64)
    buf[0:3] = PREFIX_DATA
    struct.pack_into("<H", buf, 3, addr)
    buf[8:64] = payload_bytes
    return RawReport(
        index=index,
        timestamp_ms=float(index * 10),
        report_type="output",
        report_id=0,
        length=64,
        data_hex=buf.hex()
    )


def create_mock_terminator_report(index: int) -> RawReport:
    """Create a 64-byte TERMINATOR report starting with AA 27 10 F0 03 00 01."""
    buf = bytearray(64)
    buf[0:3] = PREFIX_TERMINATOR
    struct.pack_into("<H", buf, 3, CONFIG_IMAGE_SIZE)
    buf[5:7] = bytes([0x00, 0x01])
    return RawReport(
        index=index,
        timestamp_ms=float(index * 10),
        report_type="output",
        report_id=0,
        length=64,
        data_hex=buf.hex()
    )


def build_synthetic_capture(
    custom_overrides: dict[int, int] | None = None,
    default_byte: int = 0x00,
    default_actuation: int = 0x8C,
    description: str = "Synthetic Capture",
) -> RawCapture:
    """
    Build a complete 18-chunk + terminator capture matching real hardware layout.
    Sets default actuation (0x8C) at offset +5 for the 84 active keyboard slots.
    """
    raw_config = bytearray([default_byte] * CONFIG_IMAGE_SIZE)

    for slot in ACTIVE_SLOTS_84:
        raw_config[slot * 8 + ACTUATION_OFFSET] = default_actuation

    if custom_overrides:
        for addr, val in custom_overrides.items():
            raw_config[addr] = val

    reports: list[RawReport] = []
    for i in range(CHUNK_COUNT):
        addr = i * CHUNK_PAYLOAD_SIZE
        payload = bytes(raw_config[addr : addr + CHUNK_PAYLOAD_SIZE])
        reports.append(create_mock_report_chunk(addr, payload, index=i))

    reports.append(create_mock_terminator_report(index=CHUNK_COUNT))

    return RawCapture(
        version="1.0",
        device={
            "vendor_id": f"0x{EXPECTED_VID:04X}",
            "product_id": f"0x{EXPECTED_PID:04X}",
            "product_name": "IO by Red Square Type 84 Magnetic Black"
        },
        captured_at="2026-09-14T12:00:00Z",
        description=description,
        reports_count=len(reports),
        reports=reports
    )
