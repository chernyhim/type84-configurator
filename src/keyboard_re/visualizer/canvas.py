"""
Canonical Keyboard Canvas representation for IO by Red Square Type 84 Magnetic Black.

Strictly relies on keyboard_re.ui.layout_data.TYPE84_LAYOUT as the single source of truth.
Provides continuous 2D geometry, bounding boxes, normalization, and gap handling for the
84 physical keys (17.5u width x 6.0u height).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from keyboard_re.ui.layout_data import (
    KEY_BY_LED_SLOT,
    TYPE84_LAYOUT,
    KeyDefinition,
)

# Canonical layout bounding dimensions in standard key units (u)
CANVAS_WIDTH_U: float = 17.5
CANVAS_HEIGHT_U: float = 6.0
CANVAS_ASPECT_RATIO: float = CANVAS_WIDTH_U / CANVAS_HEIGHT_U  # ~2.9167
TOTAL_PHYSICAL_KEYS: int = 84


@dataclass(frozen=True)
class KeyCanvasRegion:
    """Continuous 2D spatial region occupied by a physical key on the keyboard canvas."""
    key: KeyDefinition

    @property
    def led_slot(self) -> int:
        return self.key.led_slot

    @property
    def key_id(self) -> str:
        return self.key.key_id

    @property
    def x1(self) -> float:
        return self.key.x

    @property
    def y1(self) -> float:
        return self.key.y

    @property
    def x2(self) -> float:
        return self.key.x + self.key.width

    @property
    def y2(self) -> float:
        return self.key.y + self.key.height

    @property
    def width(self) -> float:
        return self.key.width

    @property
    def height(self) -> float:
        return self.key.height

    @property
    def center_x(self) -> float:
        return self.key.x + self.key.width / 2.0

    @property
    def center_y(self) -> float:
        return self.key.y + self.key.height / 2.0

    @property
    def normalized_bounds(self) -> Tuple[float, float, float, float]:
        """Returns (norm_x1, norm_y1, norm_x2, norm_y2) in range [0.0, 1.0]."""
        return (
            self.x1 / CANVAS_WIDTH_U,
            self.y1 / CANVAS_HEIGHT_U,
            self.x2 / CANVAS_WIDTH_U,
            self.y2 / CANVAS_HEIGHT_U,
        )

    @property
    def normalized_center(self) -> Tuple[float, float]:
        """Returns (norm_cx, norm_cy) in range [0.0, 1.0]."""
        return (
            self.center_x / CANVAS_WIDTH_U,
            self.center_y / CANVAS_HEIGHT_U,
        )

    def contains_point(self, u_x: float, u_y: float) -> bool:
        """Return True if the layout coordinate (u_x, u_y) falls inside this key."""
        return self.x1 <= u_x <= self.x2 and self.y1 <= u_y <= self.y2

    def to_pixel_box(
        self,
        pixel_width: int,
        pixel_height: int,
    ) -> Tuple[int, int, int, int]:
        """
        Map this key's region to integer pixel coordinates (px1, py1, px2, py2)
        within an image of dimensions pixel_width x pixel_height.
        Guarantees non-empty box (width >= 1, height >= 1) within valid bounds.
        """
        nx1, ny1, nx2, ny2 = self.normalized_bounds
        px1 = max(0, min(pixel_width - 1, int(round(nx1 * pixel_width))))
        py1 = max(0, min(pixel_height - 1, int(round(ny1 * pixel_height))))
        px2 = max(px1 + 1, min(pixel_width, int(round(nx2 * pixel_width))))
        py2 = max(py1 + 1, min(pixel_height, int(round(ny2 * pixel_height))))
        return (px1, py1, px2, py2)


class KeyboardCanvas:
    """
    Canonical 2D Canvas for the Type 84 layout.
    Provides structured spatial access to all 84 physical key regions.
    """

    def __init__(self) -> None:
        self._regions: List[KeyCanvasRegion] = [
            KeyCanvasRegion(key=k) for k in TYPE84_LAYOUT
        ]
        self._by_led_slot: Dict[int, KeyCanvasRegion] = {
            r.led_slot: r for r in self._regions
        }
        self._by_key_id: Dict[str, KeyCanvasRegion] = {
            r.key_id: r for r in self._regions
        }

        # Validate exactly 84 physical keys
        if len(self._regions) != TOTAL_PHYSICAL_KEYS:
            raise ValueError(
                f"Expected {TOTAL_PHYSICAL_KEYS} physical keys, got {len(self._regions)}"
            )

    @property
    def width_u(self) -> float:
        return CANVAS_WIDTH_U

    @property
    def height_u(self) -> float:
        return CANVAS_HEIGHT_U

    @property
    def aspect_ratio(self) -> float:
        return CANVAS_ASPECT_RATIO

    @property
    def total_keys(self) -> int:
        return len(self._regions)

    def regions(self) -> List[KeyCanvasRegion]:
        """Return shallow copy of all 84 key regions."""
        return list(self._regions)

    def get_by_led_slot(self, led_slot: int) -> Optional[KeyCanvasRegion]:
        return self._by_led_slot.get(led_slot)

    def get_by_key_id(self, key_id: str) -> Optional[KeyCanvasRegion]:
        return self._by_key_id.get(key_id)

    def get_key_at_point(self, u_x: float, u_y: float) -> Optional[KeyCanvasRegion]:
        """Find key covering the given unit coordinates, or None if in a gap."""
        for r in self._regions:
            if r.contains_point(u_x, u_y):
                return r
        return None

    def is_gap(self, u_x: float, u_y: float) -> bool:
        """Return True if the layout coordinate (u_x, u_y) represents a physical gap."""
        if not (0.0 <= u_x <= CANVAS_WIDTH_U and 0.0 <= u_y <= CANVAS_HEIGHT_U):
            return True
        return self.get_key_at_point(u_x, u_y) is None

    def __iter__(self) -> Iterator[KeyCanvasRegion]:
        return iter(self._regions)

    def __len__(self) -> int:
        return len(self._regions)
