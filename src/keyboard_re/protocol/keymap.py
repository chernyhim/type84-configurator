"""
Key Remapping (AA 12 / AA 16 / AA 1C) Protocol Models and Parsers for Type 84.

Pure offline parsers for the 512-byte key remapping matrix tables:
- Layer 1 (Base / Default mapping): AA 12 -> 55 12
- Layer 2 (Fn layer mapping): AA 16 -> 55 16
- Layer 3 (Alternate / Mac layer): AA 1C -> 55 1C

NO direct hardware I/O or USB transmissions.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Dict, Optional, Sequence, Tuple

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size

# Standard USB HID Usage Tables (Page 0x07 - Keyboard/Keypad)
HID_USAGE_NAMES: Dict[int, str] = {
    0x00: "None",
    0x01: "ErrorRollOver",
    0x02: "POSTFail",
    0x03: "ErrorUndefined",
    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D", 0x08: "E", 0x09: "F",
    0x0A: "G", 0x0B: "H", 0x0C: "I", 0x0D: "J", 0x0E: "K", 0x0F: "L",
    0x10: "M", 0x11: "N", 0x12: "O", 0x13: "P", 0x14: "Q", 0x15: "R",
    0x16: "S", 0x17: "T", 0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z",
    0x1E: "1!", 0x1F: "2@", 0x20: "3#", 0x21: "4$", 0x22: "5%",
    0x23: "6^", 0x24: "7&", 0x25: "8*", 0x26: "9(", 0x27: "0)",
    0x28: "Enter", 0x29: "Escape", 0x2A: "Backspace", 0x2B: "Tab",
    0x2C: "Space", 0x2D: "-_", 0x2E: "=+", 0x2F: "[{", 0x30: "]}",
    0x31: "\\|", 0x32: "NonUS#", 0x33: ";:", 0x34: "'\"", 0x35: "`~",
    0x36: ",<", 0x37: ".>", 0x38: "/?", 0x39: "CapsLock",
    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4", 0x3E: "F5", 0x3F: "F6",
    0x40: "F7", 0x41: "F8", 0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",
    0x46: "PrintScreen", 0x47: "ScrollLock", 0x48: "Pause",
    0x49: "Insert", 0x4A: "Home", 0x4B: "PageUp", 0x4C: "Delete",
    0x4D: "End", 0x4E: "PageDown", 0x4F: "RightArrow", 0x50: "LeftArrow",
    0x51: "DownArrow", 0x52: "UpArrow",
    0x53: "NumLock", 0x54: "KPSlash", 0x55: "KPAsterisk", 0x56: "KPMinus",
    0x57: "KPPlus", 0x58: "KPEnter", 0x59: "KP1", 0x5A: "KP2", 0x5B: "KP3",
    0x5C: "KP4", 0x5D: "KP5", 0x5E: "KP6", 0x5F: "KP7", 0x60: "KP8",
    0x61: "KP9", 0x62: "KP0", 0x63: "KPDot", 0x64: "NonUSBackslash",
    0x65: "Application/Menu",
    0xAF: "Fn",
    0xE0: "LCtrl", 0xE1: "LShift", 0xE2: "LAlt", 0xE3: "LGui",
    0xE4: "RCtrl", 0xE5: "RShift", 0xE6: "RAlt", 0xE7: "RGui",
}

KEYMAP_BUFFER_SIZE = 512
KEYMAP_SLOT_COUNT = 128
KEYMAP_RECORD_SIZE = 4
KEYMAP_CHUNK_COUNT = 10


@dataclass(frozen=True)
class KeyRemapRecord:
    """
    Typed 4-byte key remap record from the 512-byte matrix table.
    
    Wire layout: [page_type, param1, param2, param3]
    - Byte 0: page_type (0x02=Standard HID, 0x08=DKS, 0x03=Consumer/Extended, 0x0D=Hardware, 0x00=Unbound)
    - Byte 1: param1 (for DKS: dks_slot_index 0..63; for Consumer: key code low byte)
    - Byte 2: param2 (for Standard HID: usage scancode; for Consumer: key code high byte)
    - Byte 3: param3 (usually 0x00)
    """
    page_type: int = 0
    param1: int = 0
    param2: int = 0
    param3: int = 0

    def __init__(
        self,
        page_type: Optional[int] = None,
        param1: int = 0,
        param2: int = 0,
        param3: int = 0,
        *,
        prefix: Optional[int] = None,
        scancode: Optional[int] = None,
        special: Optional[int] = None,
        function_type: Optional[int] = None,
    ) -> None:
        if prefix is not None or scancode is not None or function_type is not None or special is not None:
            if function_type is not None and function_type != 0:
                pt = function_type
            elif prefix is not None and prefix != 0:
                pt = prefix
            elif function_type is not None:
                pt = function_type
            elif prefix is not None:
                pt = prefix
            else:
                pt = 0x02

            if pt == 0x08:  # DKS
                p1 = scancode if scancode is not None else (param1 or 0)
                p2 = 0
                p3 = param3 or 0
            elif pt == 0x0D:  # Hardware / Secondary function
                p1 = param1 or 0
                p2 = scancode or param2 or 0
                p3 = special if special is not None else (param3 or 0)
            elif pt in (0x02, 0x03):  # Keyboard / Extended
                p1 = special or 0
                p2 = scancode if scancode is not None else (param2 or 0)
                p3 = param3 or 0
            else:
                p1 = special or param1 or 0
                p2 = scancode or param2 or 0
                p3 = param3 or 0
        else:
            pt = page_type if page_type is not None else 0
            p1 = param1
            p2 = param2
            p3 = param3

        object.__setattr__(self, "page_type", pt & 0xFF)
        object.__setattr__(self, "param1", p1 & 0xFF)
        object.__setattr__(self, "param2", p2 & 0xFF)
        object.__setattr__(self, "param3", p3 & 0xFF)

    @property
    def prefix(self) -> int:
        """Alias for page_type (matching wa.DKS = 8, etc.)."""
        return self.page_type

    @property
    def scancode(self) -> int:
        """HID Usage scancode (param2 for standard keys, param1 for DKS)."""
        if self.is_dks:
            return self.param1
        return self.param2

    @property
    def special(self) -> int:
        """Param1 (special / low byte) or param3 for hardware function (type 0x0D)."""
        if self.page_type == 0x0D and self.param3 != 0:
            return self.param3
        return self.param1

    @property
    def function_type(self) -> int:
        """Page type / function type."""
        return self.page_type

    @property
    def is_empty(self) -> bool:
        """Returns True if the slot is unbound / all zeros."""
        return self.page_type == 0 and self.param1 == 0 and self.param2 == 0 and self.param3 == 0

    @property
    def is_standard_key(self) -> bool:
        """Returns True if this is a standard keyboard HID key."""
        return self.page_type in (0x02, 0x03) and self.scancode != 0

    @property
    def is_dks(self) -> bool:
        """Returns True if this key slot binds to a DKS entry (page_type 0x08)."""
        return self.page_type == 0x08

    @property
    def dks_slot_index(self) -> Optional[int]:
        """Returns DKS slot index (0..63) if this slot is a DKS binding, else None."""
        return self.param1 if self.is_dks else None

    @property
    def is_special_function(self) -> bool:
        """Returns True if this record triggers a secondary/hardware function (type 0x0D or non-zero special)."""
        return self.page_type == 0x0D or self.special != 0

    @property
    def hid_name(self) -> str:
        """Human-readable name of the HID scancode."""
        if self.is_dks:
            return f"DKS #{self.param1}"
        sc = self.scancode
        if sc != 0 and sc in HID_USAGE_NAMES:
            return HID_USAGE_NAMES[sc]
        if sc != 0:
            return f"Key_0x{sc:02X}"
        if self.special != 0:
            return f"Special_0x{self.special:02X}"
        return "None"

    def to_bytes(self) -> bytes:
        return bytes([self.page_type, self.param1, self.param2, self.param3])

    @classmethod
    def for_dks(cls, dks_slot: int) -> KeyRemapRecord:
        """Create a Remap record linking this physical key to a DKS slot (08 <dks_slot> 00 00)."""
        return cls(page_type=0x08, param1=dks_slot & 0x3F, param2=0, param3=0)

    @classmethod
    def for_standard_key(cls, scancode: int, page_type: int = 0x02) -> KeyRemapRecord:
        """Create a standard HID key remap record (02 00 <scancode> 00)."""
        return cls(page_type=page_type, param1=0, param2=scancode & 0xFF, param3=0)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> KeyRemapRecord:
        if len(data) < KEYMAP_RECORD_SIZE:
            raise ValueError(f"KeyRemapRecord requires 4 bytes, got {len(data)}")
        return cls(
            page_type=data[0],
            param1=data[1],
            param2=data[2],
            param3=data[3],
        )


@dataclass
class KeymapTable:
    """
    Reconstructed 512-byte key remap table (128 matrix slots).
    """
    layer: int  # 1 = Base (AA 12), 2 = Fn (AA 16), 3 = Alt/Mac (AA 1C)
    slots: Dict[int, KeyRemapRecord]
    raw_bytes: bytes

    def get_slot(self, bank: int, col: int) -> KeyRemapRecord:
        """Get record by matrix bank (0..7) and column (0..15)."""
        if not (0 <= bank < 8 and 0 <= col < 16):
            raise IndexError(f"Bank {bank}, Col {col} out of range (8x16)")
        slot_idx = bank * 16 + col
        return self.slots[slot_idx]

    @property
    def non_empty_count(self) -> int:
        """Count of active/bound slots in the table."""
        return sum(1 for r in self.slots.values() if not r.is_empty)

    def to_bytes(self) -> bytes:
        """Encode the 128 slots into a 512-byte binary image."""
        buf = bytearray(self.raw_bytes) if self.raw_bytes and len(self.raw_bytes) == KEYMAP_BUFFER_SIZE else bytearray(KEYMAP_BUFFER_SIZE)
        for s_idx, rec in self.slots.items():
            if 0 <= s_idx < KEYMAP_SLOT_COUNT:
                buf[s_idx * KEYMAP_RECORD_SIZE : (s_idx + 1) * KEYMAP_RECORD_SIZE] = rec.to_bytes()
        return bytes(buf)

    def set_slot(self, slot_idx: int, record: KeyRemapRecord) -> None:
        """Update a slot record and keep raw_bytes in sync."""
        if not (0 <= slot_idx < KEYMAP_SLOT_COUNT):
            raise IndexError(f"Slot index {slot_idx} out of range (0..{KEYMAP_SLOT_COUNT-1})")
        self.slots[slot_idx] = record
        self.raw_bytes = self.to_bytes()


def parse_keymap_chunks(
    reports: Sequence[bytes | bytearray],
    expected_opcode: int = 0x12,
    layer: int = 1,
) -> KeymapTable:
    """
    Parse a sequence of 10 incoming chunks into a KeymapTable.
    Works for:
    - 55 12 (Layer 1 Base)
    - 55 16 (Layer 2 Fn)
    - 55 1C (Layer 3 Alternate / Mac)
    """
    if len(reports) != KEYMAP_CHUNK_COUNT:
        raise ValueError(f"Expected {KEYMAP_CHUNK_COUNT} reports for keymap read, got {len(reports)}")

    buf = bytearray(KEYMAP_BUFFER_SIZE)
    prefix_expected = bytes([0x55, expected_opcode])

    # 1. First 9 chunks (56 bytes each)
    for i in range(9):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if rep[:2] != prefix_expected or rep[2] != 0x38:
            raise ValueError(f"Report #{i}: expected prefix {prefix_expected.hex().upper()} 38, got {rep[:3].hex().upper()}")
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * 56
        if addr != expected_addr:
            raise ValueError(f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}")
        buf[addr : addr + 56] = rep[8 : 8 + 56]

    # 2. Final tail chunk (8 bytes at 0x01F8 = 504)
    tail = bytes(reports[9])
    validate_report_size(tail, REPORT_SIZE)
    if tail[:2] != prefix_expected or tail[2] != 0x08:
        raise ValueError(f"Report #9: expected tail prefix {prefix_expected.hex().upper()} 08, got {tail[:3].hex().upper()}")
    tail_addr = struct.unpack_from("<H", tail, 3)[0]
    if tail_addr != 504:
        raise ValueError(f"Report #9: tail address mismatch, expected 0x01F8, got 0x{tail_addr:04X}")
    buf[504 : 504 + 8] = tail[8 : 8 + 8]

    # Unpack 128 slots
    slots: Dict[int, KeyRemapRecord] = {}
    for slot_idx in range(KEYMAP_SLOT_COUNT):
        offset = slot_idx * KEYMAP_RECORD_SIZE
        record_bytes = buf[offset : offset + KEYMAP_RECORD_SIZE]
        slots[slot_idx] = KeyRemapRecord.from_bytes(record_bytes)

    return KeymapTable(
        layer=layer,
        slots=slots,
        raw_bytes=bytes(buf),
    )
