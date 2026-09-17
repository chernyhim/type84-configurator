"""
Parser for 64-byte HID output reports from IO by Red Square Type 84 Magnetic Black.
"""

from __future__ import annotations

import struct
from typing import List

from keyboard_re.models import (
    CHUNK_PAYLOAD_SIZE,
    CONFIG_IMAGE_SIZE,
    MAX_CHUNK_ADDRESS,
    PREFIX_DATA,
    PREFIX_TERMINATOR,
    REPORT_SIZE,
    PacketType,
    ParsedPacket,
    RawCapture,
    RawReport,
)


def parse_single_report(raw_data: bytes, index: int = 0) -> ParsedPacket:
    """
    Parse a single raw HID output report (expected 64 bytes).
    Handles optional 1-byte Report ID prefix (65 bytes total with ID=0) by trimming.
    """
    data = raw_data

    # Handle driver prepended Report ID 0x00 (length 65)
    if len(data) == REPORT_SIZE + 1 and data[0] == 0x00:
        data = data[1:]

    if len(data) != REPORT_SIZE:
        return ParsedPacket(
            index=index,
            raw_data=data,
            packet_type=PacketType.UNKNOWN,
            header_prefix=data[:2] if len(data) >= 2 else b"",
            is_valid=False,
            error_message=f"Invalid report size: expected {REPORT_SIZE} bytes, got {len(data)}"
        )

    # 1. Check Data Chunk packet (starts with AA 27 38)
    if data.startswith(PREFIX_DATA):
        addr = struct.unpack_from("<H", data, 3)[0]
        payload = data[8:64]  # 56 bytes (8 + 56 = 64)
        padding = data[5:8]   # 3 bytes header padding

        is_valid = True
        err = None
        if len(payload) != CHUNK_PAYLOAD_SIZE:
            is_valid = False
            err = f"Data chunk payload size mismatch: expected {CHUNK_PAYLOAD_SIZE}, got {len(payload)}"
        elif addr > MAX_CHUNK_ADDRESS:
            is_valid = False
            err = f"Chunk address 0x{addr:04X} exceeds maximum expected 0x{MAX_CHUNK_ADDRESS:04X}"
        elif addr % CHUNK_PAYLOAD_SIZE != 0:
            is_valid = False
            err = f"Chunk address 0x{addr:04X} is not aligned to chunk size {CHUNK_PAYLOAD_SIZE} (0x{CHUNK_PAYLOAD_SIZE:02X})"

        return ParsedPacket(
            index=index,
            raw_data=data,
            packet_type=PacketType.DATA_CHUNK,
            header_prefix=data[:2],
            opcode_or_size=data[2],
            address=addr,
            payload=payload,
            padding=padding,
            is_valid=is_valid,
            error_message=err
        )

    # 2. Check Terminator / Commit packet (starts with AA 27 10)
    if data.startswith(PREFIX_TERMINATOR):
        term_size = struct.unpack_from("<H", data, 3)[0]
        term_flags = data[5:7]

        is_valid = True
        err = None
        if term_size != CONFIG_IMAGE_SIZE:
            err = f"Terminator reports total size {term_size} (expected {CONFIG_IMAGE_SIZE})"

        return ParsedPacket(
            index=index,
            raw_data=data,
            packet_type=PacketType.TERMINATOR,
            header_prefix=data[:2],
            opcode_or_size=data[2],
            terminator_size=term_size,
            terminator_flags=term_flags,
            padding=data[7:],
            is_valid=is_valid,
            error_message=err
        )

    # 3. Unrecognized packet
    return ParsedPacket(
        index=index,
        raw_data=data,
        packet_type=PacketType.UNKNOWN,
        header_prefix=data[:2] if len(data) >= 2 else b"",
        opcode_or_size=data[2] if len(data) >= 3 else None,
        is_valid=False,
        error_message="Unknown packet header: does not match AA 27 38 or AA 27 10"
    )


def parse_reports(reports: List[RawReport]) -> List[ParsedPacket]:
    """Parse a list of raw reports into structured ParsedPackets."""
    parsed = []
    for r in reports:
        p = parse_single_report(r.as_bytes(), index=r.index)
        parsed.append(p)
    return parsed


def parse_capture(capture: RawCapture) -> List[ParsedPacket]:
    """Parse all reports inside a RawCapture."""
    return parse_reports(capture.reports)
