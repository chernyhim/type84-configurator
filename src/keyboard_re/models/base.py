"""
Data models and constants for IO by Red Square Type 84 Magnetic Black reverse engineering.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ConfidenceLevel(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"


# Hardware & protocol constants
EXPECTED_VID = 0x0C45
EXPECTED_PID = 0x80D6

REPORT_SIZE = 64
CHUNK_PAYLOAD_SIZE = 56       # 0x38
CHUNK_COUNT = 18              # 18 chunks of 56 bytes
CONFIG_IMAGE_SIZE = 1008      # 0x03F0 = 18 * 56
SLOT_SIZE = 8                 # 8 bytes per candidate slot
SLOT_COUNT = 126              # 1008 / 8 candidate slots
MAX_CHUNK_ADDRESS = 0x03B8    # 952 (17 * 56)

ACTUATION_OFFSET = 2          # Offset of actuation uint16 inside 8-byte record (<BBHHH)
PHYSICAL_KEYS_COUNT = 84      # Exactly 84 active keys in Type 84
BANK_STRIDE = 16              # Slots per matrix row/bank (16 slots = 128 bytes)
BANK_SIZE = 128               # 128 bytes per matrix bank
BANK_HEADER_SIZE = 0          # Dense matrix addressing (no bank preamble)
KEY_RECORD_SIZE = 8           # 8 bytes per physical key (canonical <BBHHH)
BANK_COUNT = 8                # 8 banks (Banks 0..6 active, Bank 7 reserved)

PREFIX_DATA = bytes([0xAA, 0x27, 0x38])
PREFIX_TERMINATOR = bytes([0xAA, 0x27, 0x10])


from keyboard_re.protocol.hall import (
    HallKeyConfig,
    decode_mm,
    encode_mm,
    mm_to_raw,
    raw_to_mm,
)

# Backward compatibility aliases for unified conversion functions
mm_to_units = mm_to_raw
units_to_mm = raw_to_mm


def key_address(bank: int, column: int) -> int:
    """
    Calculate the absolute byte address of a key configuration record in the 1008-byte image:
    Dense physical addressing: Addr = (bank * 16 + column) * 8
    """
    if not (0 <= bank < BANK_COUNT):
        raise ValueError(f"Bank {bank} out of range [0..{BANK_COUNT-1}]")
    if not (0 <= column < 16):
        raise ValueError(f"Column {column} out of range [0..15]")
    return (bank * 16 + column) * KEY_RECORD_SIZE


def address_to_bank_col(address: int) -> Optional[Tuple[int, int]]:
    """
    Reverse calculation: given an absolute byte address, return (bank, column)
    if it points to the start of an 8-byte key record, otherwise None.
    """
    if not (0 <= address < CONFIG_IMAGE_SIZE):
        return None
    if address % KEY_RECORD_SIZE != 0:
        return None
    slot_index = address // KEY_RECORD_SIZE
    bank = slot_index // 16
    column = slot_index % 16
    if bank >= BANK_COUNT:
        return None
    return (bank, column)


@dataclass(init=False, eq=False)
class KeyConfig(HallKeyConfig):
    """
    Typed configuration structure for a single magnetic key switch (8 bytes = <BBHHH).
    Subclasses HallKeyConfig for complete backwards compatibility.

    Supports both legacy uint16 parameters (actuation=140) and physical millimeters (actuation_mm=1.40).
    """

    def __init__(
        self,
        actuation: Optional[int | float] = None,
        rt_press: Optional[int | float] = None,
        rt_release: Optional[int | float] = None,
        flags: int = 0,
        *,
        axis_type: int = 1,
        actuation_mm: Optional[float] = None,
        rt_press_mm: Optional[float] = None,
        rt_release_mm: Optional[float] = None,
    ):
        if actuation_mm is not None:
            a = float(actuation_mm)
        elif actuation is not None:
            a = raw_to_mm(actuation) if isinstance(actuation, int) else float(actuation)
        else:
            a = 1.40

        if rt_press_mm is not None:
            p = float(rt_press_mm)
        elif rt_press is not None:
            p = raw_to_mm(rt_press) if isinstance(rt_press, int) else float(rt_press)
        else:
            p = 0.00

        if rt_release_mm is not None:
            r = float(rt_release_mm)
        elif rt_release is not None:
            r = raw_to_mm(rt_release) if isinstance(rt_release, int) else float(rt_release)
        else:
            r = 0.00

        super().__init__(
            axis_type=axis_type,
            flags=flags,
            actuation_mm=a,
            rt_press_mm=p,
            rt_release_mm=r,
        )


# Complete mapping of 84 keys on the Type 84 Magnetic Black keyboard -> (bank, column)
KEY_MAP: Dict[str, Tuple[int, int]] = {
    # Bank 0: Function row (13 keys)
    "ESC": (0, 0), "F1": (0, 1), "F2": (0, 2), "F3": (0, 3), "F4": (0, 4),
    "F5": (0, 5), "F6": (0, 6), "F7": (0, 7), "F8": (0, 8), "F9": (0, 9),
    "F10": (0, 10), "F11": (0, 11), "F12": (0, 12),

    # Bank 1: Number row (13 keys)
    "GRAVE": (1, 0), "1": (1, 1), "2": (1, 2), "3": (1, 3), "4": (1, 4),
    "5": (1, 5), "6": (1, 6), "7": (1, 7), "8": (1, 8), "9": (1, 9),
    "0": (1, 10), "MINUS": (1, 11), "EQUAL": (1, 12),

    # Bank 2: QWERTY row (13 keys)
    "TAB": (2, 0), "Q": (2, 1), "W": (2, 2), "E": (2, 3), "R": (2, 4),
    "T": (2, 5), "Y": (2, 6), "U": (2, 7), "I": (2, 8), "O": (2, 9),
    "P": (2, 10), "LBRACKET": (2, 11), "RBRACKET": (2, 12),

    # Bank 3: Home row (13 keys)
    "CAPSLOCK": (3, 0), "A": (3, 1), "S": (3, 2), "D": (3, 3), "F": (3, 4),
    "G": (3, 5), "H": (3, 6), "J": (3, 7), "K": (3, 8), "L": (3, 9),
    "SEMICOLON": (3, 10), "QUOTE": (3, 11), "BACKSLASH": (3, 12),

    # Bank 4: Shift row (13 keys)
    "LSHIFT": (4, 0), "Z": (4, 1), "X": (4, 2), "C": (4, 3), "V": (4, 4),
    "B": (4, 5), "N": (4, 6), "M": (4, 7), "COMMA": (4, 8), "PERIOD": (4, 9),
    "SLASH": (4, 10), "RSHIFT": (4, 11), "ENTER": (4, 12),

    # Bank 5: Bottom row & arrows (12 keys)
    "LCTRL": (5, 0), "LWIN": (5, 1), "LALT": (5, 2), "SPACE": (5, 3),
    "RALT": (5, 4), "FN": (5, 5), "RCTRL": (5, 7),
    "LEFT": (5, 8), "DOWN": (5, 9), "UP": (5, 10), "RIGHT": (5, 11), "MENU": (5, 12),

    # Bank 6: Navigation cluster (7 keys)
    "BACKSPACE": (6, 3), "PRTSC": (6, 7), "PAUSE": (6, 8),
    "DELETE": (6, 9), "HOME": (6, 10), "PGUP": (6, 11), "PGDN": (6, 12),
}

# Synonyms and aliases for user convenience
KEY_ALIASES: Dict[str, str] = {
    "ESCAPE": "ESC",
    "CAPS": "CAPSLOCK",
    "RETURN": "ENTER",
    "DEL": "DELETE",
    "INS": "INSERT",
    "TILDE": "GRAVE",
    "`": "GRAVE",
    "~": "GRAVE",
    "-": "MINUS",
    "=": "EQUAL",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ";": "SEMICOLON",
    "'": "QUOTE",
    "\\": "BACKSLASH",
    ",": "COMMA",
    ".": "PERIOD",
    "/": "SLASH",
    "CTRL": "LCTRL",
    "ALT": "LALT",
    "WIN": "LWIN",
    "UP_ARROW": "UP",
    "DOWN_ARROW": "DOWN",
    "LEFT_ARROW": "LEFT",
    "RIGHT_ARROW": "RIGHT",
    "ARROW_UP": "UP",
    "ARROW_DOWN": "DOWN",
    "ARROW_LEFT": "LEFT",
    "ARROW_RIGHT": "RIGHT",
}


def resolve_key_name(name: str) -> Optional[str]:
    """Normalize a key name to its canonical KEY_MAP key."""
    upper = name.strip().upper()
    if upper in KEY_MAP:
        return upper
    if upper in KEY_ALIASES:
        return KEY_ALIASES[upper]
    return None


REVERSE_KEY_MAP: Dict[Tuple[int, int], str] = {pos: name for name, pos in KEY_MAP.items()}


class PacketType(str, Enum):
    DATA_CHUNK = "DATA_CHUNK"
    TERMINATOR = "TERMINATOR"
    UNKNOWN = "UNKNOWN"


@dataclass
class RawReport:
    """Represents an unmodified raw HID report as captured from the OS / WebHID."""
    index: int
    timestamp_ms: float
    report_type: str = "output"
    report_id: int = 0
    length: int = REPORT_SIZE
    data_hex: str = ""
    usage_page: Optional[str] = None
    usage: Optional[str] = None
    direction: Optional[str] = None
    event_type: Optional[str] = None
    device_id: Optional[str] = None
    device: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None

    def as_bytes(self) -> bytes:
        """Convert hex string into raw bytes."""
        clean_hex = self.data_hex.strip().replace(" ", "").replace(":", "")
        return bytes.fromhex(clean_hex)


@dataclass
class RawCapture:
    """Container for complete capture sessions, stored losslessly in JSON."""
    version: str = "1.0"
    device: Dict[str, Any] = field(default_factory=lambda: {
        "vendor_id": f"0x{EXPECTED_VID:04X}",
        "product_id": f"0x{EXPECTED_PID:04X}",
        "product_name": "IO by Red Square Type 84 Magnetic Black"
    })
    captured_at: str = ""
    description: str = ""
    reports_count: int = 0
    reports: List[RawReport] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "device": self.device,
            "captured_at": self.captured_at,
            "description": self.description,
            "reports_count": len(self.reports),
            "reports": [asdict(r) for r in self.reports]
        }

    def save_json(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path | str) -> RawCapture:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        import dataclasses
        valid_report_fields = {f.name for f in dataclasses.fields(RawReport)}
        reports = [
            RawReport(**{k: v for k, v in r.items() if k in valid_report_fields})
            for r in data.get("reports", [])
        ]
        return cls(
            version=data.get("version", "1.0"),
            device=data.get("device", {}),
            captured_at=data.get("captured_at", ""),
            description=data.get("description", ""),
            reports_count=len(reports),
            reports=reports
        )

    def extract_sessions(self) -> List[List[RawReport]]:
        """Split reports into individual 19-packet sessions if multiple bursts were captured."""
        sessions: List[List[RawReport]] = []
        current: List[RawReport] = []
        for r in self.reports:
            current.append(r)
            clean = r.data_hex.strip().replace(" ", "")
            if clean.startswith("aa2710") or clean.startswith("AA2710"):
                sessions.append(current)
                current = []
        if current:
            sessions.append(current)
        return sessions


@dataclass
class ParsedPacket:
    """Parsed representation of a 64-byte HID output report."""
    index: int
    raw_data: bytes
    packet_type: PacketType
    header_prefix: bytes
    opcode_or_size: Optional[int] = None
    address: Optional[int] = None
    payload: bytes = b""
    padding: bytes = b""
    terminator_size: Optional[int] = None
    terminator_flags: Optional[bytes] = None
    is_valid: bool = True
    error_message: Optional[str] = None


@dataclass
class KnownField:
    """Metadata for an experimentally identified field in the configuration space."""
    absolute_address: int
    name: str
    key_name: Optional[str]
    slot_index: int
    slot_offset: int
    confidence: ConfidenceLevel
    description: str


# Registry of experimentally verified fields
KNOWN_FIELDS: Dict[int, KnownField] = {
    # Key Q: confirmed experimentally (Bank 2, Col 1 -> 0x0108 + 2 = 0x010A = 266)
    0x010A: KnownField(
        absolute_address=0x010A,
        name="Actuation Point",
        key_name="Q",
        slot_index=33,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Q actuation point (Bank 2, Col 1). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key A: confirmed experimentally (Bank 3, Col 1 -> 0x0188 + 2 = 0x018A = 394)
    0x018A: KnownField(
        absolute_address=0x018A,
        name="Actuation Point",
        key_name="A",
        slot_index=49,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key A actuation point (Bank 3, Col 1). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key S: confirmed experimentally (Bank 3, Col 2 -> 0x0190 + 2 = 0x0192 = 402)
    0x0192: KnownField(
        absolute_address=0x0192,
        name="Actuation Point",
        key_name="S",
        slot_index=50,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key S actuation point (Bank 3, Col 2). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key S RT Press Sensitivity: confirmed experimentally (0x0190 + 4 = 0x0194 = 404)
    0x0194: KnownField(
        absolute_address=0x0194,
        name="RT Press Sensitivity",
        key_name="S",
        slot_index=50,
        slot_offset=4,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key S RT press sensitivity (Bank 3, Col 2, word 1). 1 unit = 0.01mm"
    ),
    # Key S RT Release Sensitivity: confirmed experimentally (0x0190 + 6 = 0x0196 = 406)
    0x0196: KnownField(
        absolute_address=0x0196,
        name="RT Release Sensitivity",
        key_name="S",
        slot_index=50,
        slot_offset=6,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key S RT release sensitivity (Bank 3, Col 2, word 2). 1 unit = 0.01mm"
    ),
    # Key D: confirmed experimentally (Bank 3, Col 3 -> 0x0198 + 2 = 0x019A = 410)
    0x019A: KnownField(
        absolute_address=0x019A,
        name="Actuation Point",
        key_name="D",
        slot_index=51,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key D actuation point (Bank 3, Col 3). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key F: confirmed experimentally (Bank 3, Col 4 -> 0x01A0 + 2 = 0x01A2 = 418)
    0x01A2: KnownField(
        absolute_address=0x01A2,
        name="Actuation Point",
        key_name="F",
        slot_index=52,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key F actuation point (Bank 3, Col 4). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Z: confirmed experimentally (Bank 4, Col 1 -> 0x0208 + 2 = 0x020A = 522)
    0x020A: KnownField(
        absolute_address=0x020A,
        name="Actuation Point",
        key_name="Z",
        slot_index=65,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Z actuation point (Bank 4, Col 1). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Enter: confirmed experimentally (Bank 4, Col 12 -> 0x0260 + 2 = 0x0262 = 610)
    0x0262: KnownField(
        absolute_address=0x0262,
        name="Actuation Point",
        key_name="Enter",
        slot_index=76,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Enter actuation point (Bank 4, Col 12). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Space: confirmed experimentally (Bank 5, Col 3 -> 0x0298 + 2 = 0x029A = 666)
    0x029A: KnownField(
        absolute_address=0x029A,
        name="Actuation Point",
        key_name="Space",
        slot_index=83,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Space actuation point (Bank 5, Col 3). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Left: confirmed experimentally (Bank 5, Col 8 -> 0x02C0 + 2 = 0x02C2 = 706)
    0x02C2: KnownField(
        absolute_address=0x02C2,
        name="Actuation Point",
        key_name="Left",
        slot_index=88,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Left arrow actuation point (Bank 5, Col 8). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Down: confirmed experimentally (Bank 5, Col 9 -> 0x02C8 + 2 = 0x02CA = 714)
    0x02CA: KnownField(
        absolute_address=0x02CA,
        name="Actuation Point",
        key_name="Down",
        slot_index=89,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Down arrow actuation point (Bank 5, Col 9). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
    # Key Right: confirmed experimentally (Bank 5, Col 11 -> 0x02D8 + 2 = 0x02DA = 730)
    0x02DA: KnownField(
        absolute_address=0x02DA,
        name="Actuation Point",
        key_name="Right",
        slot_index=91,
        slot_offset=2,
        confidence=ConfidenceLevel.CONFIRMED,
        description="Key Right arrow actuation point (Bank 5, Col 11). 1 unit = 0.01mm (0x8C = 1.40mm)"
    ),
}


@dataclass
class DiffEntry:
    """Represents a detected change between two configuration images."""
    absolute_address: int
    slot_index: int
    slot_offset: int
    old_val: int
    new_val: int
    known_field: Optional[KnownField] = None

    @property
    def old_hex(self) -> str:
        return f"0x{self.old_val:02X}"

    @property
    def new_hex(self) -> str:
        return f"0x{self.new_val:02X}"

    @property
    def delta(self) -> int:
        return self.new_val - self.old_val


class ConfigImage:
    """1008-byte monolithic configuration image assembled from 18 chunks."""

    def __init__(self, data: bytes | bytearray):
        if len(data) != CONFIG_IMAGE_SIZE:
            raise ValueError(f"ConfigImage must be exactly {CONFIG_IMAGE_SIZE} bytes, got {len(data)}")
        self._data = bytearray(data)

    @classmethod
    def empty(cls) -> ConfigImage:
        return cls(bytes(CONFIG_IMAGE_SIZE))

    @property
    def data(self) -> bytes:
        return bytes(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get_byte(self, address: int) -> int:
        if not (0 <= address < CONFIG_IMAGE_SIZE):
            raise IndexError(f"Address 0x{address:04X} out of bounds [0..{CONFIG_IMAGE_SIZE-1}]")
        return self._data[address]

    def set_byte(self, address: int, val: int) -> None:
        if not (0 <= address < CONFIG_IMAGE_SIZE):
            raise IndexError(f"Address 0x{address:04X} out of bounds [0..{CONFIG_IMAGE_SIZE-1}]")
        self._data[address] = val & 0xFF

    def get_slot(self, slot_index: int) -> bytes:
        if not (0 <= slot_index < SLOT_COUNT):
            raise IndexError(f"Slot index {slot_index} out of bounds [0..{SLOT_COUNT-1}]")
        start = slot_index * SLOT_SIZE
        return bytes(self._data[start : start + SLOT_SIZE])

    def all_slots(self) -> List[bytes]:
        return [self.get_slot(i) for i in range(SLOT_COUNT)]

    def get_key_config(self, key_name: str) -> KeyConfig:
        """Get typed configuration for a key by name (e.g. 'S', 'A', 'Enter', 'Space')."""
        canon = resolve_key_name(key_name)
        if not canon:
            raise KeyError(f"Unknown key name: '{key_name}'")
        bank, col = KEY_MAP[canon]
        return self.get_key_config_by_bank_col(bank, col)

    def set_key_config(self, key_name: str, config: KeyConfig) -> None:
        """Set typed configuration for a key by name."""
        canon = resolve_key_name(key_name)
        if not canon:
            raise KeyError(f"Unknown key name: '{key_name}'")
        bank, col = KEY_MAP[canon]
        self.set_key_config_by_bank_col(bank, col, config)

    def get_key_config_by_bank_col(self, bank: int, column: int) -> KeyConfig:
        """Get typed configuration for a key by (bank, column)."""
        addr = key_address(bank, column)
        return KeyConfig.from_bytes(self._data[addr : addr + KEY_RECORD_SIZE])

    def set_key_config_by_bank_col(self, bank: int, column: int, config: KeyConfig) -> None:
        """Set typed configuration for a key by (bank, column)."""
        addr = key_address(bank, column)
        self._data[addr : addr + KEY_RECORD_SIZE] = config.to_bytes()

    def all_key_configs(self) -> Dict[str, KeyConfig]:
        """Extract typed configurations for all 84 mapped physical keys."""
        return {name: self.get_key_config(name) for name in KEY_MAP}

