"""
Game Mode / Performance Settings (AA 11 / AA 21) Production Pipeline for Type 84.

Protocol:
- Read Opcode: AA 11 (returns 55 11 38 00 00 00 01 00 + 56B payload)
- Write Opcode: AA 21 (sends AA 21 38 00 00 00 01 00 + 56B payload)
- Expected ACK: 55 21 38 00 00 00 01 00 + 56B payload echo
- Single report (56B payload in 64B HID report).
"""

from __future__ import annotations

from typing import Dict, Optional

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.plan import WriteChunk, build_chunk_packet, build_expected_ack
from keyboard_re.protocol.read import GameModeResponse
from keyboard_re.protocol.transport import HidTransport

GAME_MODE_READ_OPCODE: int = 0x11
GAME_MODE_WRITE_OPCODE: int = 0x21
GAME_MODE_PAYLOAD_SIZE: int = 56

REPORT_RATE_TO_HZ: Dict[int, int] = {
    3: 1000,
    4: 2000,
    5: 4000,
    6: 8000,
}

HZ_TO_REPORT_RATE: Dict[int, int] = {
    1000: 3,
    2000: 4,
    4000: 5,
    8000: 6,
}


def build_game_mode_write_packet(config: GameModeResponse) -> bytes:
    """Build a 64-byte HID output report for AA 21 write."""
    payload = config.to_payload()
    return build_chunk_packet(
        opcode=GAME_MODE_WRITE_OPCODE,
        size=GAME_MODE_PAYLOAD_SIZE,
        address=0,
        payload=payload,
        is_last=True,
    )


def build_game_mode_expected_ack(config: GameModeResponse) -> bytes:
    """Build the expected 64-byte ACK report for AA 21 write."""
    payload = config.to_payload()
    return build_expected_ack(
        opcode=GAME_MODE_WRITE_OPCODE,
        size=GAME_MODE_PAYLOAD_SIZE,
        address=0,
        payload=payload,
        is_last=True,
    )


def build_game_mode_write_chunk(config: GameModeResponse) -> WriteChunk:
    """Build single WriteChunk for ProfileWritePlan integration."""
    pkt = build_game_mode_write_packet(config)
    ack = build_game_mode_expected_ack(config)
    return WriteChunk(
        chunk_index=0,
        address=0,
        size=GAME_MODE_PAYLOAD_SIZE,
        packet=pkt,
        expected_ack=ack,
    )


def validate_game_mode_ack(
    ack: bytes | bytearray,
    expected_payload: Optional[bytes] = None,
    strict_payload: bool = True,
) -> Optional[str]:
    """Validate incoming 64-byte ACK report against expected AA 21 response."""
    if len(ack) != REPORT_SIZE:
        return f"Invalid report size {len(ack)}, expected {REPORT_SIZE}"

    rep = bytes(ack)
    if rep[0] != 0x55:
        return f"Invalid ACK prefix 0x{rep[0]:02X}, expected 0x55"
    if rep[1] != GAME_MODE_WRITE_OPCODE:
        return f"Invalid ACK opcode 0x{rep[1]:02X}, expected 0x{GAME_MODE_WRITE_OPCODE:02X}"
    if rep[2] != GAME_MODE_PAYLOAD_SIZE:
        return f"Invalid ACK size 0x{rep[2]:02X}, expected 0x{GAME_MODE_PAYLOAD_SIZE:02X}"

    # Header flags: 00 00 00 01 00 (addr=0, is_last=1)
    if rep[3:8] != bytes([0x00, 0x00, 0x00, 0x01, 0x00]):
        return f"Invalid ACK header flags: {rep[3:8].hex(' ').upper()}"

    if strict_payload and expected_payload is not None:
        actual_payload = rep[8 : 8 + GAME_MODE_PAYLOAD_SIZE]
        if actual_payload != expected_payload:
            diff_indices = [
                i for i in range(len(expected_payload)) if actual_payload[i] != expected_payload[i]
            ]
            return f"ACK payload echo mismatch at offsets {diff_indices}"

    return None


def write_game_mode(
    transport: HidTransport,
    config: GameModeResponse,
    timeout: float = 1.0,
    strict_ack: bool = True,
) -> bytes:
    """
    Transmit AA 21 write packet to physical keyboard and synchronously validate ACK.

    Returns the raw 64-byte ACK response received from the hardware.
    """
    pkt = build_game_mode_write_packet(config)
    transport.send_report(0, pkt)
    ack = transport.receive_report(timeout=timeout)

    expected_payload = config.to_payload()
    err = validate_game_mode_ack(ack, expected_payload=expected_payload, strict_payload=strict_ack)
    if err:
        raise ValueError(f"GameMode write ACK validation failed: {err}")

    return bytes(ack)
