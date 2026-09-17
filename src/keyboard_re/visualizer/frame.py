"""
RGBFrame model for Keyboard Visualizer.

Encapsulates the 84 physical key RGB color state, 512-byte LED buffer serialization,
and differential frame analysis for transmission optimization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, Optional, Sequence, Set, Tuple

from keyboard_re.protocol.rgb import (
    LED_BUFFER_SIZE,
    LED_SLOT_COUNT,
    LED_SLOT_SIZE,
    RGB_PER_KEY_CHUNK_PAYLOAD_SIZE,
    build_led_buffer,
    parse_led_buffer,
)
from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT


def slot_to_chunk_index(slot: int) -> int:
    """
    Map an LED slot index (0..127) to its AA24 wire chunk index (0..9).
    Chunks 0..8 cover 14 slots each (14 * 4 = 56 bytes).
    Chunk 9 covers slots 126 and 127 (8 bytes).
    """
    slots_per_chunk = RGB_PER_KEY_CHUNK_PAYLOAD_SIZE // LED_SLOT_SIZE  # 56 // 4 = 14
    chunk = slot // slots_per_chunk
    return min(chunk, 9)


@dataclass
class RGBFrame:
    """
    Represents an atomic RGB frame for the keyboard matrix.
    Stores RGB color (0..255, 0..255, 0..255) for all 84 physical keys.
    """
    key_colors: Dict[int, Tuple[int, int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize: ensure all 84 physical keys have an assigned color (default (0, 0, 0))
        for slot in KEY_BY_LED_SLOT:
            if slot not in self.key_colors:
                self.key_colors[slot] = (0, 0, 0)

    def get_color(self, led_slot: int) -> Tuple[int, int, int]:
        """Return (R, G, B) for the given LED slot, or (0, 0, 0) if unassigned."""
        return self.key_colors.get(led_slot, (0, 0, 0))

    def set_color(self, led_slot: int, color: Tuple[int, int, int]) -> None:
        """Set (R, G, B) for the given LED slot."""
        r, g, b = color
        self.key_colors[led_slot] = (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        )

    def to_led_buffer(self) -> bytes:
        """
        Serialize this frame into the confirmed 512-byte Per-Key LED buffer (AA24).
        Non-key slots are filled with (0, 0, 0, slot_id).
        """
        return build_led_buffer(self.key_colors)

    def diff_slots(self, other: RGBFrame) -> Set[int]:
        """
        Compare with another frame and return the set of LED slot indices
        whose RGB colors differ.
        """
        changed: Set[int] = set()
        for slot in KEY_BY_LED_SLOT:
            if self.get_color(slot) != other.get_color(slot):
                changed.add(slot)
        return changed

    def diff_chunks(self, other: RGBFrame) -> Set[int]:
        """
        Compare with another frame and return the set of AA24 wire chunk indices
        (0..9) that contain one or more changed slots.
        """
        changed_slots = self.diff_slots(other)
        return {slot_to_chunk_index(s) for s in changed_slots}

    def clone(self) -> RGBFrame:
        """Return a deep copy of this frame."""
        return RGBFrame(key_colors=dict(self.key_colors))

    @classmethod
    def black(cls) -> RGBFrame:
        """Create an all-black frame (all 84 physical keys off)."""
        return cls(key_colors={slot: (0, 0, 0) for slot in KEY_BY_LED_SLOT})

    @classmethod
    def solid(cls, color: Tuple[int, int, int]) -> RGBFrame:
        """Create a frame where all 84 physical keys have the specified color."""
        r = max(0, min(255, int(color[0])))
        g = max(0, min(255, int(color[1])))
        b = max(0, min(255, int(color[2])))
        return cls(key_colors={slot: (r, g, b) for slot in KEY_BY_LED_SLOT})

    @classmethod
    def from_led_buffer(cls, buffer: bytes | bytearray) -> RGBFrame:
        """
        Parse a 512-byte Per-Key LED buffer and reconstruct an RGBFrame
        filtering to the 84 physical keys.
        """
        slot_map = parse_led_buffer(buffer)
        frame = cls()
        for slot in KEY_BY_LED_SLOT:
            if slot in slot_map:
                frame.set_color(slot, slot_map[slot])
        return frame

    def __iter__(self) -> Iterator[Tuple[int, Tuple[int, int, int]]]:
        for slot in sorted(KEY_BY_LED_SLOT.keys()):
            yield slot, self.get_color(slot)

    def __len__(self) -> int:
        return len(KEY_BY_LED_SLOT)
