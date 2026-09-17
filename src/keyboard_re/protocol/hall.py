"""
Hall Effect / Analog Matrix Protocol Encoder & Decoder (AA 27).

Pure offline protocol implementation for building and parsing:
- 56-byte data chunk reports: AA 27 38 <addr_lo> <addr_hi> ...
- Full 18-report write sequences for 1008-byte configuration images
- Terminator / Commit reports: AA 27 10 F0 03 00 01 ...

NO hardware transmission or direct HID write operations.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Dict, Optional, Sequence

from keyboard_re.protocol.packets import REPORT_SIZE, pad_report, validate_report_size

# Hall Effect protocol dimensions
HALL_IMAGE_SIZE: int = 1008
HALL_CHUNK_PAYLOAD_SIZE: int = 56  # 0x38 bytes
HALL_CHUNK_COUNT: int = 18
HALL_MAX_CHUNK_ADDRESS: int = 952  # 17 * 56 = 0x03B8
KEY_RECORD_SIZE: int = 8           # 8 bytes per key record (4x uint16_le)

# Opcodes and prefixes
HALL_CHUNK_PREFIX: bytes = b"\xAA\x27\x38"
HALL_TERMINATOR_PREFIX: bytes = b"\xAA\x27\x10"
HALL_TERMINATOR_DEFAULT_FLAGS: bytes = b"\x00\x01"


def mm_to_raw(value_mm: float) -> int:
    """
    Convert a millimeter measurement to a 16-bit unsigned integer (0.01 mm step).
    Empirically confirmed on physical IO Type 84 Magnetic Black keyboard:
    raw = round(value_mm * 100)
    Clamped to uint16 range [0, 65535].
    """
    return max(0, min(65535, int(round(value_mm * 100.0))))


def raw_to_mm(raw: int) -> float:
    """
    Convert a 16-bit raw unsigned integer to a millimeter measurement (0.01 mm step).
    Empirically confirmed on physical IO Type 84 Magnetic Black keyboard:
    mm = raw / 100.0
    """
    return round((raw & 0xFFFF) / 100.0, 2)


def encode_mm(value_mm: float) -> bytes:
    """Encode a millimeter value into exactly 2 bytes little-endian (<H)."""
    return struct.pack("<H", mm_to_raw(value_mm))


def decode_mm(data: bytes | bytearray, offset: int = 0) -> float:
    """Decode 2 bytes little-endian (<H) into a millimeter value."""
    raw = struct.unpack_from("<H", data, offset)[0]
    return raw_to_mm(raw)


@dataclass
class HallKeyConfig:
    """
    Production-level typed model for a single Hall Effect switch configuration record.

    Canonical wire format: strictly 8 bytes = <BBHHH
    - +0: axis_type (uint8, default 1)
    - +1: flags (uint8, bit 0 = Rapid Trigger enabled / isWholeFast)
    - +2..3: actuation (uint16 LE, 0.01 mm step)
    - +4..5: rt_press (uint16 LE, 0.01 mm step)
    - +6..7: rt_release (uint16 LE, 0.01 mm step)
    """
    axis_type: int = 1
    flags: int = 0x00
    actuation_mm: float = 1.40
    rt_press_mm: float = 0.00
    rt_release_mm: float = 0.00

    @property
    def actuation_raw(self) -> int:
        return mm_to_raw(self.actuation_mm)

    @property
    def rt_press_raw(self) -> int:
        return mm_to_raw(self.rt_press_mm)

    @property
    def rt_release_raw(self) -> int:
        return mm_to_raw(self.rt_release_mm)

    # Legacy raw integer properties
    @property
    def actuation(self) -> int:
        return self.actuation_raw

    @actuation.setter
    def actuation(self, val: int) -> None:
        self.actuation_mm = raw_to_mm(val)

    @property
    def rt_press(self) -> int:
        return self.rt_press_raw

    @rt_press.setter
    def rt_press(self, val: int) -> None:
        self.rt_press_mm = raw_to_mm(val)

    @property
    def rt_release(self) -> int:
        return self.rt_release_raw

    @rt_release.setter
    def rt_release(self, val: int) -> None:
        self.rt_release_mm = raw_to_mm(val)

    @property
    def is_rt_enabled(self) -> bool:
        """Rapid Trigger enabled flag: strictly flags & 0x01 (vendor isWholeFast)."""
        return bool(self.flags & 0x01)

    @is_rt_enabled.setter
    def is_rt_enabled(self, enabled: bool) -> None:
        if enabled:
            self.flags |= 0x01
        else:
            self.flags &= ~0x01

    def disable_rt(self) -> None:
        """Disable Rapid Trigger by clearing bit 0 of flags."""
        self.flags &= ~0x01

    def enable_rt(self, press_mm: float = 0.20, release_mm: float = 0.20) -> None:
        """Enable Rapid Trigger (sets bit 0 of flags) with specific sensitivities in millimeters."""
        self.flags |= 0x01
        if press_mm > 0.0:
            self.rt_press_mm = press_mm
        if release_mm > 0.0:
            self.rt_release_mm = release_mm

    def to_bytes(self) -> bytes:
        """
        Encode key configuration into exactly 8 bytes (<BBHHH):
        +0: axis_type (uint8)
        +1: flags     (uint8, bit 0 = Rapid Trigger enabled)
        +2..3: Actuation (raw uint16 LE)
        +4..5: RT Press  (raw uint16 LE)
        +6..7: RT Release (raw uint16 LE)
        """
        return struct.pack(
            "<BBHHH",
            self.axis_type & 0xFF,
            self.flags & 0xFF,
            self.actuation_raw,
            self.rt_press_raw,
            self.rt_release_raw,
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> HallKeyConfig:
        """Decode 8 bytes (<BBHHH) into a typed HallKeyConfig."""
        if len(data) < KEY_RECORD_SIZE:
            raise ValueError(f"HallKeyConfig requires at least {KEY_RECORD_SIZE} bytes, got {len(data)}")
        axis_type, flg, act, press, rel = struct.unpack("<BBHHH", data[:KEY_RECORD_SIZE])
        return cls(
            axis_type=axis_type,
            flags=flg,
            actuation_mm=raw_to_mm(act),
            rt_press_mm=raw_to_mm(press),
            rt_release_mm=raw_to_mm(rel),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HallKeyConfig):
            return False
        return (
            (self.axis_type & 0xFF) == (other.axis_type & 0xFF)
            and (self.flags & 0xFF) == (other.flags & 0xFF)
            and self.actuation_raw == other.actuation_raw
            and self.rt_press_raw == other.rt_press_raw
            and self.rt_release_raw == other.rt_release_raw
        )


def encode_hall_record(config: HallKeyConfig) -> bytes:
    """Encode a HallKeyConfig into an 8-byte record."""
    return config.to_bytes()


def decode_hall_record(data: bytes | bytearray, offset: int = 0) -> HallKeyConfig:
    """Decode an 8-byte record from buffer at offset into a HallKeyConfig."""
    return HallKeyConfig.from_bytes(data[offset : offset + KEY_RECORD_SIZE])


def decode_hall_image(image: bytes | bytearray) -> Dict[str, HallKeyConfig]:
    """
    Decode all 84 physical keys from a 1008-byte Hall configuration image into typed HallKeyConfig records.
    """
    from keyboard_re.models.base import KEY_MAP, key_address

    if len(image) < HALL_IMAGE_SIZE:
        raise ValueError(f"Image must be at least {HALL_IMAGE_SIZE} bytes, got {len(image)}")

    result: Dict[str, HallKeyConfig] = {}
    for key_name, (bank, col) in KEY_MAP.items():
        addr = key_address(bank, col)
        result[key_name] = decode_hall_record(image, addr)
    return result


def encode_hall_image(
    keys: Dict[str, HallKeyConfig],
    base_image: Optional[bytes | bytearray] = None,
) -> bytes:
    """
    Encode an 84-key mapping of HallKeyConfig records into a 1008-byte image.
    Preserves bank headers and unmapped regions if base_image is provided.
    """
    from keyboard_re.models.base import KEY_MAP, key_address

    buf = bytearray(base_image[:HALL_IMAGE_SIZE]) if base_image else bytearray(HALL_IMAGE_SIZE)
    for key_name, (bank, col) in KEY_MAP.items():
        if key_name in keys:
            addr = key_address(bank, col)
            buf[addr : addr + KEY_RECORD_SIZE] = encode_hall_record(keys[key_name])
    return bytes(buf)


def build_hall_chunk(address: int, payload: bytes | bytearray) -> bytes:
    """
    Build a single 64-byte HID output report for a 56-byte configuration chunk.

    Structure:
    - Bytes 0..2: AA 27 38 (Prefix + chunk length 0x38)
    - Bytes 3..4: Address (uint16 little-endian)
    - Bytes 5..7: Zero header padding (3 bytes: 00 00 00)
    - Bytes 8..63: Payload (exactly 56 bytes)
    Total length: exactly 64 bytes.
    """
    if len(payload) != HALL_CHUNK_PAYLOAD_SIZE:
        raise ValueError(
            f"Hall chunk payload must be exactly {HALL_CHUNK_PAYLOAD_SIZE} bytes, got {len(payload)}"
        )
    if not (0 <= address <= HALL_MAX_CHUNK_ADDRESS):
        raise ValueError(
            f"Hall chunk address 0x{address:04X} out of range [0, 0x{HALL_MAX_CHUNK_ADDRESS:04X}]"
        )
    if address % HALL_CHUNK_PAYLOAD_SIZE != 0:
        raise ValueError(
            f"Hall chunk address 0x{address:04X} is not aligned to chunk size {HALL_CHUNK_PAYLOAD_SIZE}"
        )

    pkt = bytearray(REPORT_SIZE)
    pkt[0] = 0xAA
    pkt[1] = 0x27
    pkt[2] = 0x38
    struct.pack_into("<H", pkt, 3, address)
    pkt[8 : 8 + len(payload)] = payload
    return bytes(pkt)


def build_hall_write(image: bytes | bytearray) -> list[bytes]:
    """
    Build the complete sequence of 18 HID output reports for a 1008-byte configuration image.

    Validates:
    - Image is exactly 1008 bytes.
    - Produces exactly 18 chunks.
    - Addresses step by 0x38 (0, 56, 112, ..., 952).
    - Every report is exactly 64 bytes.
    """
    if len(image) != HALL_IMAGE_SIZE:
        raise ValueError(
            f"Configuration image must be exactly {HALL_IMAGE_SIZE} bytes, got {len(image)}"
        )

    reports: list[bytes] = []
    for i in range(HALL_CHUNK_COUNT):
        addr = i * HALL_CHUNK_PAYLOAD_SIZE
        payload = bytes(image[addr : addr + HALL_CHUNK_PAYLOAD_SIZE])
        report = build_hall_chunk(addr, payload)
        reports.append(report)

    return reports


def build_hall_terminator(
    total_size: int = HALL_IMAGE_SIZE,
    flags: bytes = HALL_TERMINATOR_DEFAULT_FLAGS
) -> bytes:
    """
    Build the 64-byte terminator / commit HID output report.

    Structure:
    - Bytes 0..2: AA 27 10
    - Bytes 3..4: Image size uint16_le (default 1008 = 0x03F0)
    - Bytes 5..6: Commit flags (default b"\x00\x01")
    - Bytes 7..63: Zero padding (57 bytes)
    Total length: exactly 64 bytes.
    """
    if len(flags) != 2:
        raise ValueError(f"Terminator flags must be exactly 2 bytes, got {len(flags)}")

    payload = HALL_TERMINATOR_PREFIX + struct.pack("<H", total_size) + flags
    return pad_report(payload, REPORT_SIZE)


def parse_hall_chunk(report: bytes | bytearray) -> tuple[int, bytes]:
    """
    Parse a single 64-byte Hall data chunk report.
    Returns (address, payload_56_bytes).
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(HALL_CHUNK_PREFIX):
        raise ValueError(
            f"Invalid Hall chunk prefix: expected {HALL_CHUNK_PREFIX.hex().upper()}, got {rep[:3].hex().upper()}"
        )

    addr = struct.unpack_from("<H", rep, 3)[0]
    payload = rep[8 : 8 + HALL_CHUNK_PAYLOAD_SIZE]
    return addr, payload


def parse_hall_terminator(report: bytes | bytearray) -> tuple[int, bytes]:
    """
    Parse a 64-byte Hall terminator report.
    Returns (total_size, flags_2_bytes).
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(HALL_TERMINATOR_PREFIX):
        raise ValueError(
            f"Invalid Hall terminator prefix: expected {HALL_TERMINATOR_PREFIX.hex().upper()}, got {rep[:3].hex().upper()}"
        )

    total_size = struct.unpack_from("<H", rep, 3)[0]
    flags = rep[5:7]
    return total_size, flags


def parse_hall_write(reports: Sequence[bytes | bytearray]) -> bytes:
    """
    Parse and reassemble an 18-report write sequence into a 1008-byte image.
    If a 19th report is present, validates it as the terminator.
    """
    if len(reports) not in (HALL_CHUNK_COUNT, HALL_CHUNK_COUNT + 1):
        raise ValueError(
            f"Expected {HALL_CHUNK_COUNT} or {HALL_CHUNK_COUNT + 1} reports, got {len(reports)}"
        )

    buffer = bytearray(HALL_IMAGE_SIZE)
    for i in range(HALL_CHUNK_COUNT):
        addr, payload = parse_hall_chunk(reports[i])
        expected_addr = i * HALL_CHUNK_PAYLOAD_SIZE
        if addr != expected_addr:
            raise ValueError(
                f"Chunk #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}"
            )
        buffer[addr : addr + HALL_CHUNK_PAYLOAD_SIZE] = payload

    if len(reports) == HALL_CHUNK_COUNT + 1:
        parse_hall_terminator(reports[HALL_CHUNK_COUNT])

    return bytes(buffer)
