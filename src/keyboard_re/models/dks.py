"""
DKS (Dynamic Keystroke) Protocol Models and Parsers for Type 84.

===============================================================================
EVIDENCE CLASSIFICATION & PROTOCOL STATUS:
===============================================================================
1. TIER 1: PHYSICALLY CONFIRMED:
   - AA 18 (GET_MAGNETIC_AXIS_DKS_DATA): 1024-byte read query in 19 chunks (18*56B + 1*16B).
   - AA 28 (SET_MAGNETIC_AXIS_DKS_DATA): 1024-byte write command in 19 chunks (18*56B + 1*16B).
   - Response / ACK: 55 18 and 55 28.
   - Buffer size: exactly 1024 bytes (64 record slots * 16 bytes).
   - Indirection via Remap Layer 1: physical key binding maps to DKS via prefix=0x08 (wa.DKS)
     and scancode=dks_slot_index (0..63). Complete subsystem isolation from other keys.
   - Byte +0: makeValue1 travel threshold in 0.1 mm increments (physically confirmed via
     semantic ABA write/readback 1.5mm -> 1.6mm -> 1.5mm).
   - Bytes +1..+3: makeValue2, breakValue1, breakValue2 travel thresholds (0.1 mm increments).
   - Bytes +4, +6, +8, +10: zero padding bytes (0x00) preceding action scancodes.
   - Byte +5: Action 1 USB HID scancode (confirmed 0x1A for 'W').
   - Byte +7: Action 2 USB HID scancode (confirmed 0xE1 for 'LShift' in Exp C).
   - State bytes +12..+15: Dedicated state bytes for Points 0, 1, 2, 3 (all physically confirmed):
     * Byte +12 = Point 0 (makeValue1)
     * Byte +13 = Point 1 (makeValue2, confirmed via C1/C2)
     * Byte +14 = Point 2 (breakValue1, confirmed via D1/D2)
     * Byte +15 = Point 3 (breakValue2, confirmed via E1/E2)
   - Nibble architecture (confirmed on all 4 travel points):
     * Low nibble bits [0..3] = TAP / SINGLE actuation
     * High nibble bits [4..7] = HOLD continuous hold
   - Action bit mapping (all physically confirmed on hardware):
     * Action 1 -> bit 0: TAP = 0x01 (Exp A), HOLD = 0x10 (Exp B)
     * Action 2 -> bit 1: TAP = 0x02 (Exp C), HOLD = 0x20 (Final Control Test)
     * Action 3 -> bit 2: TAP = 0x04 (Exp A1), HOLD = 0x40 (Exp A2)
     * Action 4 -> bit 3: TAP = 0x08 (Exp B1), HOLD = 0x80 (Exp B2)

2. TIER 2: STRUCTURAL INFERENCE:
   - Action 3 scancode at byte +9 and Action 4 scancode at byte +11 (inferred from factory layout pattern).

3. TIER 3: VENDOR JS EVIDENCE / UNVERIFIED PHYSICAL SEMANTICS:
   - Collision precedence when both TAP and HOLD bits are set simultaneously for an action
     in a single state byte (e.g. 0x11 for Action 1):
     * Vendor JS (layout-classic.js:328010) checks 'single & bit' first (TAP precedence).
     * Python decoder checks 'hold & bit' first (HOLD precedence).
     * Physical hardware EEPROM behavior under collision has never been observed or tested
       (vendor UI never generates collision states; valid bits are mutually exclusive).
     * The Python decoder deliberately preserves HOLD-before-TAP precedence without alteration.

4. TIER 4: MODEL ASSUMPTIONS:
   - UI 3-state cycling: OFF -> TAP -> HOLD -> OFF via (curr + 1) % 3 in DKSView.
   - Default travel thresholds on newly enabled slots: 1.6 / 3.0 / 3.0 / 1.6 mm.
   - Travel slider limits: 0.1..3.9 mm for make1/break2, 0.2..4.0 mm for make2/break1.
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Sequence


DKS_BUFFER_SIZE = 1024
DKS_RECORD_SIZE = 16
DKS_SLOT_COUNT = 64

# Vendor defaults (0.1 mm scale)
DEFAULT_MAKE_VALUE_1_MM = 1.6
DEFAULT_MAKE_VALUE_2_MM = 3.0
DEFAULT_BREAK_VALUE_1_MM = 3.0
DEFAULT_BREAK_VALUE_2_MM = 1.6


class DKSEventState(IntEnum):
    """
    Action state in a specific travel point (Tier 1: Physically Confirmed across all 4 actions & 4 points).
    - TAP: Single actuation event (low nibble bitmask, bits [0..3]).
           Physically confirmed: Act 1 (0x01), Act 2 (0x02), Act 3 (0x04), Act 4 (0x08).
    - HOLD: Continuous hold across travel stages (high nibble bitmask, bits [4..7]).
            Physically confirmed: Act 1 (0x10), Act 2 (0x20), Act 3 (0x40), Act 4 (0x80).
    """
    OFF = 0
    TAP = 1   # Single actuation event (low nibble bitmask)
    HOLD = 2  # Continuous hold across travel stages (high nibble bitmask)


@dataclass
class DKSRecord:
    """
    16-byte Dynamic Keystroke (DKS) record.

    Byte layout:
    - Byte 0: makeValue1 (Tier 1: Physically Confirmed, uint8 in 0.1 mm)
    - Byte 1: makeValue2 (Tier 1: Physically Confirmed, uint8 in 0.1 mm)
    - Byte 2: breakValue1 (Tier 1: Physically Confirmed, uint8 in 0.1 mm)
    - Byte 3: breakValue2 (Tier 1: Physically Confirmed, uint8 in 0.1 mm)
    - Byte 4: reserved / 0x00 padding (Tier 1: Physically Confirmed)
    - Byte 5: action1 HID scancode (Tier 1: Physically Confirmed)
    - Byte 6: reserved / 0x00 padding (Tier 1: Physically Confirmed)
    - Byte 7: action2 HID scancode (Tier 1: Physically Confirmed)
    - Byte 8: reserved / 0x00 padding (Tier 1: Physically Confirmed)
    - Byte 9: action3 HID scancode (Tier 2: Structural Inference)
    - Byte 10: reserved / 0x00 padding (Tier 1: Physically Confirmed)
    - Byte 11: action4 HID scancode (Tier 2: Structural Inference)
    - Byte 12: state byte for Point 0 / make1 (Tier 1: Physically Confirmed)
    - Byte 13: state byte for Point 1 / make2 (Tier 1: Physically Confirmed via C1/C2)
    - Byte 14: state byte for Point 2 / break1 (Tier 1: Physically Confirmed via D1/D2)
    - Byte 15: state byte for Point 3 / break2 (Tier 1: Physically Confirmed via E1/E2)
    """
    make_value_1_mm: float = DEFAULT_MAKE_VALUE_1_MM
    make_value_2_mm: float = DEFAULT_MAKE_VALUE_2_MM
    break_value_1_mm: float = DEFAULT_BREAK_VALUE_1_MM
    break_value_2_mm: float = DEFAULT_BREAK_VALUE_2_MM
    actions: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    # 4 rows for 4 actions, 4 cols for 4 travel points
    states: List[List[DKSEventState]] = field(
        default_factory=lambda: [[DKSEventState.OFF] * 4 for _ in range(4)]
    )
    # Preserves bytes 4, 6, 8, 10 verbatim
    raw_reserved: bytes = b"\x00\x00\x00\x00"

    @property
    def is_empty(self) -> bool:
        """Returns True if the slot contains no configured actions or travel thresholds."""
        has_actions = any(a != 0 for a in self.actions)
        has_states = any(s != DKSEventState.OFF for row in self.states for s in row)
        has_travel = (
            self.make_value_1_mm > 0
            or self.make_value_2_mm > 0
            or self.break_value_1_mm > 0
            or self.break_value_2_mm > 0
        )
        return not has_actions and not has_states and not has_travel

    def to_bytes(self) -> bytes:
        """
        Encode record into exactly 16 bytes conforming to physically confirmed wire layout:
        (single_bits & 0x0F) | ((hold_bits & 0x0F) << 4).
        """
        buf = bytearray(DKS_RECORD_SIZE)
        if self.is_empty:
            return bytes(buf)

        buf[0] = int(round(self.make_value_1_mm * 10)) & 0xFF
        buf[1] = int(round(self.make_value_2_mm * 10)) & 0xFF
        buf[2] = int(round(self.break_value_1_mm * 10)) & 0xFF
        buf[3] = int(round(self.break_value_2_mm * 10)) & 0xFF

        res = self.raw_reserved if len(self.raw_reserved) == 4 else b"\x00\x00\x00\x00"
        buf[4] = res[0]
        buf[5] = self.actions[0] & 0xFF if len(self.actions) > 0 else 0
        buf[6] = res[1]
        buf[7] = self.actions[1] & 0xFF if len(self.actions) > 1 else 0
        buf[8] = res[2]
        buf[9] = self.actions[2] & 0xFF if len(self.actions) > 2 else 0
        buf[10] = res[3]
        buf[11] = self.actions[3] & 0xFF if len(self.actions) > 3 else 0

        # Encode 4 point state bytes (+12..+15)
        for h in range(4):
            single_bits = 0
            hold_bits = 0
            for a_idx in range(min(4, len(self.states))):
                st = self.states[a_idx][h] if h < len(self.states[a_idx]) else DKSEventState.OFF
                if st == DKSEventState.TAP:
                    single_bits |= 1 << a_idx
                elif st == DKSEventState.HOLD:
                    hold_bits |= 1 << a_idx
            buf[12 + h] = (single_bits & 0x0F) | ((hold_bits & 0x0F) << 4)

        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> DKSRecord:
        """
        Decode 16-byte record following physically confirmed wire format.
        """
        if len(data) < DKS_RECORD_SIZE:
            raise ValueError(f"DKSRecord requires 16 bytes, got {len(data)}")

        r = bytes(data[:DKS_RECORD_SIZE])
        if all(b == 0 for b in r):
            return cls(
                make_value_1_mm=0.0,
                make_value_2_mm=0.0,
                break_value_1_mm=0.0,
                break_value_2_mm=0.0,
                actions=[0, 0, 0, 0],
                states=[[DKSEventState.OFF] * 4 for _ in range(4)],
                raw_reserved=b"\x00\x00\x00\x00",
            )

        mk1 = r[0] / 10.0
        mk2 = r[1] / 10.0
        br1 = r[2] / 10.0
        br2 = r[3] / 10.0

        reserved = bytes([r[4], r[6], r[8], r[10]])
        actions = [r[5], r[7], r[9], r[11]]

        # Decode travel point state bytes (+12..+15)
        # Each byte r[12+h] has low nibble (single/tap) and high nibble (hold).
        # Tier 1 Physically Confirmed:
        #   - Byte +12 (Point 0), Byte +13 (Point 1), Byte +14 (Point 2), Byte +15 (Point 3).
        #   - Low nibble bits [0..3]: Action 1 TAP (0x01), Action 2 TAP (0x02), Action 3 TAP (0x04), Action 4 TAP (0x08).
        #   - High nibble bits [4..7]: Action 1 HOLD (0x10), Action 2 HOLD (0x20), Action 3 HOLD (0x40), Action 4 HOLD (0x80).
        # Tier 3 (Collision Precedence):
        #   If a synthetic/corrupted byte has both TAP and HOLD bits set for an action (e.g. 0x11):
        #   Vendor JS (layout-classic.js:328010) checks 'single & bit' first (TAP precedence).
        #   Python decoder checks 'hold & bit' first (HOLD precedence).
        #   Physical hardware behavior under collision is unverified (vendor UI never emits collision bits).
        #   We deliberately preserve Python's HOLD-before-TAP precedence here.
        states = [[DKSEventState.OFF] * 4 for _ in range(4)]
        for h in range(4):
            val = r[12 + h]
            single = val & 0x0F
            hold = (val >> 4) & 0x0F
            for a_idx in range(4):
                bit = 1 << a_idx
                if hold & bit:
                    states[a_idx][h] = DKSEventState.HOLD
                elif single & bit:
                    states[a_idx][h] = DKSEventState.TAP
                else:
                    states[a_idx][h] = DKSEventState.OFF

        return cls(
            make_value_1_mm=mk1,
            make_value_2_mm=mk2,
            break_value_1_mm=br1,
            break_value_2_mm=br2,
            actions=actions,
            states=states,
            raw_reserved=reserved,
        )


@dataclass
class DKSTable:
    """
    Structured 1024-byte DKS table containing 64 record slots.
    """
    records: Dict[int, DKSRecord] = field(default_factory=dict)

    def get_record(self, index: int) -> DKSRecord:
        if index not in self.records:
            return DKSRecord(
                make_value_1_mm=0.0,
                make_value_2_mm=0.0,
                break_value_1_mm=0.0,
                break_value_2_mm=0.0,
            )
        return self.records[index]

    def set_record(self, index: int, record: DKSRecord) -> None:
        if index < 0 or index >= DKS_SLOT_COUNT:
            raise IndexError(f"DKS slot index {index} out of bounds (0..{DKS_SLOT_COUNT - 1})")
        self.records[index] = record

    def clear_record(self, index: int) -> None:
        if index in self.records:
            del self.records[index]

    def allocate_slot(self) -> Optional[int]:
        """
        Find first unallocated or empty slot (0..63).
        """
        for i in range(DKS_SLOT_COUNT):
            if i not in self.records or self.records[i].is_empty:
                return i
        return None

    def clone(self) -> DKSTable:
        cloned_records = {
            k: DKSRecord(
                make_value_1_mm=v.make_value_1_mm,
                make_value_2_mm=v.make_value_2_mm,
                break_value_1_mm=v.break_value_1_mm,
                break_value_2_mm=v.break_value_2_mm,
                actions=list(v.actions),
                states=[list(row) for row in v.states],
                raw_reserved=v.raw_reserved,
            )
            for k, v in self.records.items()
        }
        return DKSTable(records=cloned_records)

    def to_bytes(self) -> bytes:
        """
        Serialize 64 slots into full 1024-byte table.
        """
        buf = bytearray(DKS_BUFFER_SIZE)
        for i in range(DKS_SLOT_COUNT):
            rec = self.records.get(i)
            if rec and not rec.is_empty:
                buf[i * DKS_RECORD_SIZE : (i + 1) * DKS_RECORD_SIZE] = rec.to_bytes()
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> DKSTable:
        """
        Parse 1024-byte raw buffer into DKSTable.
        """
        if len(data) < DKS_BUFFER_SIZE:
            raise ValueError(f"DKSTable requires at least {DKS_BUFFER_SIZE} bytes, got {len(data)}")

        records: Dict[int, DKSRecord] = {}
        for i in range(DKS_SLOT_COUNT):
            chunk = data[i * DKS_RECORD_SIZE : (i + 1) * DKS_RECORD_SIZE]
            rec = DKSRecord.from_bytes(chunk)
            if not rec.is_empty:
                records[i] = rec
        return cls(records=records)
