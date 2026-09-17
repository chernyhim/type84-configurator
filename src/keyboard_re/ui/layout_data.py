"""
Physical Keyboard Layout Data for IO by Red Square Type 84 Magnetic Black.

Defines the 84 physical keys in a standard 75% compact ANSI layout (16.0u width).
Strictly separates:
- switch_slot: Hardware switch index (0..127) used by Key Remapping (L1/L2) and Hall Effect.
- led_slot: Hardware LED index (0..127) used by the 512-byte RGB Matrix buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class KeyDefinition:
    """Definition of a physical key in the Type 84 layout."""
    key_id: str
    label: str
    switch_slot: int
    led_slot: int
    row: int
    col: int
    x: float
    y: float
    width: float = 1.0
    height: float = 1.0

    @property
    def remap_slot(self) -> int:
        """Hardware slot index in 512-byte KeymapTable (AA 12 / AA 22). 1:1 with switch_slot."""
        return self.switch_slot



# 84 Physical Keys of IO Type 84 with physical navigation cluster (17.5u width)
TYPE84_LAYOUT: List[KeyDefinition] = [
    # Row 0: Function Row & Top Navigation (16 keys)
    KeyDefinition("ESC", "Esc", switch_slot=0, led_slot=0, row=0, col=0, x=0.0, y=0.0),
    KeyDefinition("F1", "F1", switch_slot=1, led_slot=1, row=0, col=1, x=1.0, y=0.0),
    KeyDefinition("F2", "F2", switch_slot=2, led_slot=2, row=0, col=2, x=2.0, y=0.0),
    KeyDefinition("F3", "F3", switch_slot=3, led_slot=3, row=0, col=3, x=3.0, y=0.0),
    KeyDefinition("F4", "F4", switch_slot=4, led_slot=4, row=0, col=4, x=4.0, y=0.0),
    KeyDefinition("F5", "F5", switch_slot=5, led_slot=5, row=0, col=5, x=5.0, y=0.0),
    KeyDefinition("F6", "F6", switch_slot=6, led_slot=6, row=0, col=6, x=6.0, y=0.0),
    KeyDefinition("F7", "F7", switch_slot=7, led_slot=7, row=0, col=7, x=7.0, y=0.0),
    KeyDefinition("F8", "F8", switch_slot=8, led_slot=8, row=0, col=8, x=8.0, y=0.0),
    KeyDefinition("F9", "F9", switch_slot=9, led_slot=9, row=0, col=9, x=9.0, y=0.0),
    KeyDefinition("F10", "F10", switch_slot=10, led_slot=10, row=0, col=10, x=10.0, y=0.0),
    KeyDefinition("F11", "F11", switch_slot=11, led_slot=11, row=0, col=11, x=11.0, y=0.0),
    KeyDefinition("F12", "F12", switch_slot=12, led_slot=12, row=0, col=12, x=12.0, y=0.0),
    KeyDefinition("PRTSC", "Prt", switch_slot=99, led_slot=13, row=0, col=13, x=14.0, y=0.0),
    KeyDefinition("HOME", "Home", switch_slot=104, led_slot=14, row=0, col=14, x=15.5, y=0.0),
    KeyDefinition("END", "End", switch_slot=107, led_slot=15, row=0, col=15, x=16.5, y=0.0),

    # Row 1: Number Row & Upper Navigation (16 keys)
    KeyDefinition("GRAVE", "` ~", switch_slot=16, led_slot=16, row=1, col=0, x=0.0, y=1.0),
    KeyDefinition("1", "1 !", switch_slot=17, led_slot=17, row=1, col=1, x=1.0, y=1.0),
    KeyDefinition("2", "2 @", switch_slot=18, led_slot=18, row=1, col=2, x=2.0, y=1.0),
    KeyDefinition("3", "3 #", switch_slot=19, led_slot=19, row=1, col=3, x=3.0, y=1.0),
    KeyDefinition("4", "4 $", switch_slot=20, led_slot=20, row=1, col=4, x=4.0, y=1.0),
    KeyDefinition("5", "5 %", switch_slot=21, led_slot=21, row=1, col=5, x=5.0, y=1.0),
    KeyDefinition("6", "6 ^", switch_slot=22, led_slot=22, row=1, col=6, x=6.0, y=1.0),
    KeyDefinition("7", "7 &", switch_slot=23, led_slot=23, row=1, col=7, x=7.0, y=1.0),
    KeyDefinition("8", "8 *", switch_slot=24, led_slot=24, row=1, col=8, x=8.0, y=1.0),
    KeyDefinition("9", "9 (", switch_slot=25, led_slot=25, row=1, col=9, x=9.0, y=1.0),
    KeyDefinition("0", "0 )", switch_slot=26, led_slot=26, row=1, col=10, x=10.0, y=1.0),
    KeyDefinition("MINUS", "- _", switch_slot=27, led_slot=27, row=1, col=11, x=11.0, y=1.0),
    KeyDefinition("EQUAL", "= +", switch_slot=28, led_slot=28, row=1, col=12, x=12.0, y=1.0),
    KeyDefinition("BACKSPACE", "Back", switch_slot=92, led_slot=29, row=1, col=13, x=13.0, y=1.0, width=2.0),
    KeyDefinition("INS", "Ins", switch_slot=103, led_slot=31, row=1, col=14, x=15.5, y=1.0),
    KeyDefinition("PGUP", "PgUp", switch_slot=105, led_slot=32, row=1, col=15, x=16.5, y=1.0),

    # Row 2: QWERTY Row & Mid Navigation (16 keys)
    KeyDefinition("TAB", "Tab", switch_slot=32, led_slot=33, row=2, col=0, x=0.0, y=2.0, width=1.5),
    KeyDefinition("Q", "Q", switch_slot=33, led_slot=34, row=2, col=1, x=1.5, y=2.0),
    KeyDefinition("W", "W", switch_slot=34, led_slot=35, row=2, col=2, x=2.5, y=2.0),
    KeyDefinition("E", "E", switch_slot=35, led_slot=36, row=2, col=3, x=3.5, y=2.0),
    KeyDefinition("R", "R", switch_slot=36, led_slot=37, row=2, col=4, x=4.5, y=2.0),
    KeyDefinition("T", "T", switch_slot=37, led_slot=38, row=2, col=5, x=5.5, y=2.0),
    KeyDefinition("Y", "Y", switch_slot=38, led_slot=39, row=2, col=6, x=6.5, y=2.0),
    KeyDefinition("U", "U", switch_slot=39, led_slot=40, row=2, col=7, x=7.5, y=2.0),
    KeyDefinition("I", "I", switch_slot=40, led_slot=41, row=2, col=8, x=8.5, y=2.0),
    KeyDefinition("O", "O", switch_slot=41, led_slot=42, row=2, col=9, x=9.5, y=2.0),
    KeyDefinition("P", "P", switch_slot=42, led_slot=43, row=2, col=10, x=10.5, y=2.0),
    KeyDefinition("LBRACKET", "[ {", switch_slot=43, led_slot=44, row=2, col=11, x=11.5, y=2.0),
    KeyDefinition("RBRACKET", "] }", switch_slot=44, led_slot=45, row=2, col=12, x=12.5, y=2.0),
    KeyDefinition("BACKSLASH", "\\ |", switch_slot=60, led_slot=46, row=2, col=13, x=13.5, y=2.0, width=1.5),
    KeyDefinition("DEL", "Del", switch_slot=106, led_slot=47, row=2, col=14, x=15.5, y=2.0),
    KeyDefinition("PGDN", "PgDn", switch_slot=108, led_slot=48, row=2, col=15, x=16.5, y=2.0),

    # Row 3: Home Row (13 keys)
    KeyDefinition("CAPSLOCK", "Caps", switch_slot=48, led_slot=49, row=3, col=0, x=0.0, y=3.0, width=1.75),
    KeyDefinition("A", "A", switch_slot=49, led_slot=50, row=3, col=1, x=1.75, y=3.0),
    KeyDefinition("S", "S", switch_slot=50, led_slot=51, row=3, col=2, x=2.75, y=3.0),
    KeyDefinition("D", "D", switch_slot=51, led_slot=52, row=3, col=3, x=3.75, y=3.0),
    KeyDefinition("F", "F", switch_slot=52, led_slot=53, row=3, col=4, x=4.75, y=3.0),
    KeyDefinition("G", "G", switch_slot=53, led_slot=54, row=3, col=5, x=5.75, y=3.0),
    KeyDefinition("H", "H", switch_slot=54, led_slot=55, row=3, col=6, x=6.75, y=3.0),
    KeyDefinition("J", "J", switch_slot=55, led_slot=56, row=3, col=7, x=7.75, y=3.0),
    KeyDefinition("K", "K", switch_slot=56, led_slot=57, row=3, col=8, x=8.75, y=3.0),
    KeyDefinition("L", "L", switch_slot=57, led_slot=58, row=3, col=9, x=9.75, y=3.0),
    KeyDefinition("SEMICOLON", "; :", switch_slot=58, led_slot=59, row=3, col=10, x=10.75, y=3.0),
    KeyDefinition("QUOTE", "' \"", switch_slot=59, led_slot=60, row=3, col=11, x=11.75, y=3.0),
    KeyDefinition("ENTER", "Enter", switch_slot=76, led_slot=61, row=3, col=12, x=12.75, y=3.0, width=2.25),

    # Row 4: Shift Row & Up Arrow (13 keys)
    KeyDefinition("LSHIFT", "L-Shift", switch_slot=64, led_slot=63, row=4, col=0, x=0.0, y=4.0, width=2.25),
    KeyDefinition("Z", "Z", switch_slot=65, led_slot=65, row=4, col=1, x=2.25, y=4.0),
    KeyDefinition("X", "X", switch_slot=66, led_slot=66, row=4, col=2, x=3.25, y=4.0),
    KeyDefinition("C", "C", switch_slot=67, led_slot=67, row=4, col=3, x=4.25, y=4.0),
    KeyDefinition("V", "V", switch_slot=68, led_slot=68, row=4, col=4, x=5.25, y=4.0),
    KeyDefinition("B", "B", switch_slot=69, led_slot=69, row=4, col=5, x=6.25, y=4.0),
    KeyDefinition("N", "N", switch_slot=70, led_slot=70, row=4, col=6, x=7.25, y=4.0),
    KeyDefinition("M", "M", switch_slot=71, led_slot=71, row=4, col=7, x=8.25, y=4.0),
    KeyDefinition("COMMA", ", <", switch_slot=72, led_slot=72, row=4, col=8, x=9.25, y=4.0),
    KeyDefinition("PERIOD", ". >", switch_slot=73, led_slot=73, row=4, col=9, x=10.25, y=4.0),
    KeyDefinition("SLASH", "/ ?", switch_slot=74, led_slot=74, row=4, col=10, x=11.25, y=4.0),
    KeyDefinition("RSHIFT", "R-Shift", switch_slot=75, led_slot=75, row=4, col=11, x=12.25, y=4.0, width=2.25),
    KeyDefinition("UP", "↑", switch_slot=90, led_slot=76, row=4, col=12, x=15.5, y=4.0),

    # Row 5: Bottom Modifier Row & Directional Arrows (10 keys)
    KeyDefinition("LCTRL", "L-Ctrl", switch_slot=80, led_slot=77, row=5, col=0, x=0.0, y=5.0, width=1.25),
    KeyDefinition("LWIN", "L-Win", switch_slot=81, led_slot=78, row=5, col=1, x=1.25, y=5.0, width=1.25),
    KeyDefinition("LALT", "L-Alt", switch_slot=82, led_slot=79, row=5, col=2, x=2.5, y=5.0, width=1.25),
    KeyDefinition("SPACE", "Space", switch_slot=83, led_slot=80, row=5, col=3, x=3.75, y=5.0, width=6.25),
    KeyDefinition("RALT", "R-Alt", switch_slot=84, led_slot=81, row=5, col=4, x=10.0, y=5.0, width=1.0),
    KeyDefinition("FN", "Fn", switch_slot=85, led_slot=82, row=5, col=5, x=11.0, y=5.0, width=1.0),
    KeyDefinition("RCTRL", "R-Ctrl", switch_slot=87, led_slot=83, row=5, col=6, x=12.0, y=5.0, width=1.0),
    KeyDefinition("LEFT", "←", switch_slot=88, led_slot=84, row=5, col=7, x=14.5, y=5.0, width=1.0),
    KeyDefinition("DOWN", "↓", switch_slot=89, led_slot=85, row=5, col=8, x=15.5, y=5.0, width=1.0),
    KeyDefinition("RIGHT", "→", switch_slot=91, led_slot=86, row=5, col=9, x=16.5, y=5.0, width=1.0),
]

# Quick lookup indexes
KEY_BY_SWITCH_SLOT: Dict[int, KeyDefinition] = {k.switch_slot: k for k in TYPE84_LAYOUT}
KEY_BY_LED_SLOT: Dict[int, KeyDefinition] = {k.led_slot: k for k in TYPE84_LAYOUT}
KEY_BY_ID: Dict[str, KeyDefinition] = {k.key_id: k for k in TYPE84_LAYOUT}
KEY_BY_REMAP_SLOT: Dict[int, KeyDefinition] = {k.switch_slot: k for k in TYPE84_LAYOUT}
PHYSICAL_84_REMAP_SLOTS: frozenset[int] = frozenset(k.switch_slot for k in TYPE84_LAYOUT)
PHYSICAL_84_SWITCH_SLOTS: frozenset[int] = frozenset(k.switch_slot for k in TYPE84_LAYOUT)
FN_REMAP_SLOT: int = 85  # switch_slot 85 (Fn is physical switch 85)
FN_DISABLED_SWITCH_SLOTS: frozenset[int] = frozenset(range(1, 13))  # F1..F12 (switch slots 1..12, protected in Fn layer)

# Firmware fallback default scancodes for keys that appear with scancode 0 in baseline
FIRMWARE_FALLBACK_DEFAULTS: Dict[str, Tuple[int, str]] = {
    "MINUS": (0x2D, "- _"),
    "O": (0x12, "O"),
    "J": (0x0D, "J"),
    "B": (0x05, "B"),
    "SPACE": (0x2C, "Space"),
}


def get_function_type_for_scancode(scancode: int) -> int:
    """
    Explicitly resolve function_type based on verified hardware baseline:
    - 0x03: Page Down (0x4E) and Keypad codes (0x53..0x63).
    - 0x02: Standard HID keyboard keys.
    """
    if scancode == 0x4E or (0x53 <= scancode <= 0x63):
        return 0x03
    return 0x02

