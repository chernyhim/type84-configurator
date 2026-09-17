"""
High-Level RGB Matrix Model & Editor for IO by Red Square Type 84 Magnetic Black.

Manages the 512-byte Per-Key LED configuration buffer (128 slots x 4 bytes [R, G, B, LED_ID]).
Provides strict validation, slot editing, physical key mapping resolution,
diffing, and deterministic serialization.
NO physical HID writes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from keyboard_re.models.rgb_editor import parse_color_input
from keyboard_re.protocol.rgb import (
    LED_BUFFER_SIZE,
    LED_SLOT_COUNT,
    LED_SLOT_SIZE,
    PHYSICAL_LED_MAP,
    build_rgb_per_key_chunks,
    get_led_index,
)


class RGBMatrix:
    """
    Mutable in-memory model of the 512-byte Per-Key RGB matrix.
    
    Wire format per slot (4 bytes):
      Byte 0: Red (0..255)
      Byte 1: Green (0..255)
      Byte 2: Blue (0..255)
      Byte 3: LED_ID (slot index 0..127)
    
    Guarantees:
    - Buffer length is strictly 512 bytes;
    - Preserves LED_ID and unknown trailing bytes on slot edits;
    - Strictly validates slot indices [0, 127] and RGB ranges [0, 255];
    - Clean separation between logical LED slots and physical key labels.
    """

    def __init__(self, raw_buffer: Optional[bytes | bytearray] = None) -> None:
        if raw_buffer is not None:
            if len(raw_buffer) != LED_BUFFER_SIZE:
                raise ValueError(
                    f"RGBMatrix buffer must be exactly {LED_BUFFER_SIZE} bytes, got {len(raw_buffer)}"
                )
            self._buffer = bytearray(raw_buffer)
        else:
            # Initialize default 512-byte buffer: all black with canonical LED_ID = i
            self._buffer = bytearray(LED_BUFFER_SIZE)
            for i in range(LED_SLOT_COUNT):
                self._buffer[i * LED_SLOT_SIZE + 3] = i & 0xFF

    # -------------------------------------------------------------------------
    # Factories
    # -------------------------------------------------------------------------

    @classmethod
    def from_raw(cls, data: bytes | bytearray) -> RGBMatrix:
        """Create RGBMatrix from a 512-byte raw binary buffer."""
        return cls(data)

    @classmethod
    def from_hex(cls, hex_str: str) -> RGBMatrix:
        """Create RGBMatrix from a hex string."""
        raw = bytes.fromhex(hex_str.strip())
        return cls.from_raw(raw)

    @classmethod
    def create_empty(cls) -> RGBMatrix:
        """Create an empty (all LEDs off) RGB matrix."""
        return cls()

    @classmethod
    def create_filled(cls, color: Union[Tuple[int, int, int], Sequence[int], str]) -> RGBMatrix:
        """Create an RGB matrix with all LEDs set to the given color."""
        matrix = cls()
        matrix.fill(color)
        return matrix

    # -------------------------------------------------------------------------
    # Inspection
    # -------------------------------------------------------------------------

    def get_led(self, slot: int) -> Tuple[int, int, int]:
        """Get the (R, G, B) color tuple of a logical LED slot (0..127)."""
        self._validate_slot(slot)
        off = slot * LED_SLOT_SIZE
        return (self._buffer[off], self._buffer[off + 1], self._buffer[off + 2])

    def get_led_id(self, slot: int) -> int:
        """Get the hardware LED_ID byte of a logical LED slot (0..127)."""
        self._validate_slot(slot)
        return self._buffer[slot * LED_SLOT_SIZE + 3]

    def get_key_led(self, key_name: str) -> Tuple[int, int, int]:
        """
        Get the (R, G, B) color tuple for a confirmed physical key name.
        Raises KeyError if the physical key mapping is unconfirmed.
        """
        slot = get_led_index(key_name)
        if slot is None:
            raise KeyError(f"Physical LED mapping for key '{key_name}' is not confirmed")
        return self.get_led(slot)

    # -------------------------------------------------------------------------
    # Mutation
    # -------------------------------------------------------------------------

    def set_led(
        self,
        slot: int,
        color: Union[Tuple[int, int, int], Sequence[int], str],
    ) -> RGBMatrix:
        """
        Set color for a logical LED slot (0..127).
        Preserves the slot's existing LED_ID byte.
        """
        self._validate_slot(slot)
        r, g, b = parse_color_input(color)
        off = slot * LED_SLOT_SIZE
        self._buffer[off] = r
        self._buffer[off + 1] = g
        self._buffer[off + 2] = b
        return self

    def set_key_led(
        self,
        key_name: str,
        color: Union[Tuple[int, int, int], Sequence[int], str],
    ) -> RGBMatrix:
        """
        Set color for a confirmed physical key name.
        Uses confirmed PHYSICAL_LED_MAP. Raises KeyError if key is unconfirmed.
        """
        slot = get_led_index(key_name)
        if slot is None:
            raise KeyError(f"Physical LED mapping for key '{key_name}' is not confirmed")
        return self.set_led(slot, color)

    def set_many(
        self,
        mapping: Dict[int, Union[Tuple[int, int, int], Sequence[int], str]],
    ) -> RGBMatrix:
        """Update multiple slots simultaneously by slot index."""
        for slot, col in mapping.items():
            self.set_led(slot, col)
        return self

    def set_many_keys(
        self,
        mapping: Dict[str, Union[Tuple[int, int, int], Sequence[int], str]],
    ) -> RGBMatrix:
        """Update multiple keys simultaneously by physical key name."""
        for key_name, col in mapping.items():
            self.set_key_led(key_name, col)
        return self

    def fill(self, color: Union[Tuple[int, int, int], Sequence[int], str]) -> RGBMatrix:
        """
        Set all 128 LED slots to the given color.
        Preserves each slot's LED_ID byte.
        """
        r, g, b = parse_color_input(color)
        for i in range(LED_SLOT_COUNT):
            off = i * LED_SLOT_SIZE
            self._buffer[off] = r
            self._buffer[off + 1] = g
            self._buffer[off + 2] = b
        return self

    def clear(self) -> RGBMatrix:
        """Turn off all 128 LEDs (set to RGB 0, 0, 0). Preserves LED_ID bytes."""
        return self.fill((0, 0, 0))

    # -------------------------------------------------------------------------
    # Comparison & Serialization
    # -------------------------------------------------------------------------

    def diff(
        self,
        other: RGBMatrix,
    ) -> Dict[int, Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """
        Compare this matrix against another.
        Returns a mapping of slot_id -> (this_rgb, other_rgb) for differing slots.
        """
        differences: Dict[int, Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = {}
        for s in range(LED_SLOT_COUNT):
            c_a = self.get_led(s)
            c_b = other.get_led(s)
            if c_a != c_b:
                differences[s] = (c_a, c_b)
        return differences

    def to_raw(self) -> bytes:
        """Export the exact 512-byte raw binary matrix buffer."""
        return bytes(self._buffer)

    def build(self) -> bytes:
        """Alias for to_raw() providing consistent builder interface."""
        return self.to_raw()

    def to_chunks(self) -> List[bytes]:
        """
        Encode this matrix into the 10-report write sequence (AA 24)
        ready for USB HID transmission.
        """
        return build_rgb_per_key_chunks(self.to_raw())

    def to_dict(self) -> Dict[str, Any]:
        """Export matrix to dictionary representation with hex and non-zero slots."""
        active_slots: Dict[str, List[int]] = {}
        for s in range(LED_SLOT_COUNT):
            col = self.get_led(s)
            if col != (0, 0, 0):
                active_slots[str(s)] = list(col)

        return {
            "hex": self._buffer.hex(),
            "active_led_count": len(active_slots),
            "slots": active_slots,
        }

    def clone(self) -> RGBMatrix:
        """Return an independent copy of this RGBMatrix."""
        return RGBMatrix(bytes(self._buffer))

    def __len__(self) -> int:
        return LED_SLOT_COUNT

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, RGBMatrix):
            return self._buffer == other._buffer
        if isinstance(other, (bytes, bytearray)):
            return self._buffer == other
        return False

    def __getitem__(self, slot: int) -> Tuple[int, int, int]:
        return self.get_led(slot)

    def __setitem__(
        self,
        slot: int,
        color: Union[Tuple[int, int, int], Sequence[int], str],
    ) -> None:
        self.set_led(slot, color)

    def __iter__(self) -> Iterator[Tuple[int, Tuple[int, int, int]]]:
        for s in range(LED_SLOT_COUNT):
            yield (s, self.get_led(s))

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _validate_slot(self, slot: int) -> None:
        if not isinstance(slot, int) or not (0 <= slot < LED_SLOT_COUNT):
            raise IndexError(
                f"LED slot index {slot} out of range [0, {LED_SLOT_COUNT - 1}]"
            )
