"""
Experimental slot structure analyzer.
Evaluates configuration diffs across sessions to map 8-byte candidate slots
WITHOUT making unverified assumptions about unknown bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from keyboard_re.differ import diff_images
from keyboard_re.models import (
    KEY_MAP,
    KNOWN_FIELDS,
    SLOT_COUNT,
    SLOT_SIZE,
    ConfidenceLevel,
    ConfigImage,
    DiffEntry,
    KeyConfig,
    KnownField,
    key_address,
)


@dataclass
class ByteObservation:
    """Observed behavior of a specific byte in an 8-byte candidate slot."""
    offset: int
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    field_name: Optional[str] = None
    key_name: Optional[str] = None
    description: str = "Unmapped byte"
    change_events: List[str] = field(default_factory=list)


@dataclass
class SlotStructure:
    """Structure representation of a candidate 8-byte slot."""
    slot_index: int
    base_address: int
    bytes_info: List[ByteObservation] = field(default_factory=list)

    @classmethod
    def create_empty(cls, slot_index: int) -> SlotStructure:
        base = slot_index * SLOT_SIZE
        obs = []
        for off in range(SLOT_SIZE):
            addr = base + off
            known = KNOWN_FIELDS.get(addr)
            if known:
                obs.append(
                    ByteObservation(
                        offset=off,
                        confidence=known.confidence,
                        field_name=known.name,
                        key_name=known.key_name,
                        description=known.description,
                    )
                )
            else:
                obs.append(
                    ByteObservation(
                        offset=off,
                        confidence=ConfidenceLevel.UNKNOWN,
                        field_name=None,
                        key_name=None,
                        description="Unknown/untested byte",
                    )
                )
        return cls(slot_index=slot_index, base_address=base, bytes_info=obs)


class ExperimentalAnalyzer:
    """
    Experimental analyzer that correlates diffs with known user actions
    to build an empirically verified map of slots and fields.
    """

    def __init__(self):
        self.slots: Dict[int, SlotStructure] = {
            i: SlotStructure.create_empty(i) for i in range(SLOT_COUNT)
        }

    def record_experiment(
        self,
        base_img: ConfigImage,
        mod_img: ConfigImage,
        action_description: str,
        target_key: Optional[str] = None,
        parameter_name: Optional[str] = None,
    ) -> List[DiffEntry]:
        """
        Analyze the diff between base and modified images resulting from a single controlled GUI action.
        """
        diffs = diff_images(base_img, mod_img)

        for d in diffs:
            slot_struct = self.slots[d.slot_index]
            byte_obs = slot_struct.bytes_info[d.slot_offset]
            evt = (
                f"Action: '{action_description}' | "
                f"Addr: 0x{d.absolute_address:04X} | "
                f"Changed: {d.old_hex} -> {d.new_hex} (delta {d.delta:+d})"
            )
            byte_obs.change_events.append(evt)

            if len(diffs) == 1 and target_key and parameter_name:
                byte_obs.key_name = target_key
                byte_obs.field_name = parameter_name
                byte_obs.confidence = ConfidenceLevel.CONFIRMED
                byte_obs.description = f"Empirically confirmed via isolated action: {action_description}"

        return diffs

    def get_summary(self) -> Dict[str, Any]:
        """Produce a summary of all confirmed, probable, and unknown fields."""
        confirmed_count = 0
        probable_count = 0
        unknown_count = 0
        mapped_slots = []

        for slot_idx, slot in self.slots.items():
            slot_active = False
            for b in slot.bytes_info:
                if b.confidence == ConfidenceLevel.CONFIRMED:
                    confirmed_count += 1
                    slot_active = True
                elif b.confidence == ConfidenceLevel.PROBABLE:
                    probable_count += 1
                    slot_active = True
                else:
                    unknown_count += 1
                    if b.change_events:
                        slot_active = True

            if slot_active:
                mapped_slots.append(slot)

        return {
            "total_slots": SLOT_COUNT,
            "confirmed_fields": confirmed_count,
            "probable_fields": probable_count,
            "unknown_fields": unknown_count,
            "active_slots_count": len(mapped_slots),
        }

    def format_slot_report(self, slot_index: int) -> str:
        """Format detailed status of an 8-byte candidate slot."""
        if not (0 <= slot_index < SLOT_COUNT):
            return f"Invalid slot index {slot_index}"

        s = self.slots[slot_index]
        lines = [
            f"=== Slot {slot_index:03d} (Base Address: 0x{s.base_address:04X} / {s.base_address:4d}) ===",
            f"{'Offset':<8} {'Address':<10} {'Key':<10} {'Field Name':<22} {'Confidence':<14} {'Description'}",
            "-" * 90,
        ]

        for b in s.bytes_info:
            addr = s.base_address + b.offset
            key = b.key_name or "-"
            fname = b.field_name or "-"
            conf = f"[{b.confidence.value}]"
            lines.append(f"+{b.offset:<7} 0x{addr:04X}     {key:<10} {fname:<22} {conf:<14} {b.description}")

        return "\n".join(lines)


@dataclass
class KeyConfigDiff:
    """Semantic difference for a physical key between two ConfigImages."""
    key_name: str
    bank: int
    column: int
    address: int
    old_config: KeyConfig
    new_config: KeyConfig

    @property
    def actuation_changed(self) -> bool:
        return self.old_config.actuation != self.new_config.actuation

    @property
    def rt_press_changed(self) -> bool:
        return self.old_config.rt_press != self.new_config.rt_press

    @property
    def rt_release_changed(self) -> bool:
        return self.old_config.rt_release != self.new_config.rt_release

    @property
    def flags_changed(self) -> bool:
        return self.old_config.flags != self.new_config.flags


def diff_key_configs(base_img: ConfigImage, mod_img: ConfigImage) -> List[KeyConfigDiff]:
    """
    Compare all 84 physical keys between base and modified ConfigImages.
    Returns list of KeyConfigDiff for keys that changed.
    """
    diffs: List[KeyConfigDiff] = []
    for key_name, (bank, col) in KEY_MAP.items():
        old_cfg = base_img.get_key_config_by_bank_col(bank, col)
        new_cfg = mod_img.get_key_config_by_bank_col(bank, col)
        if old_cfg != new_cfg:
            addr = key_address(bank, col)
            diffs.append(
                KeyConfigDiff(
                    key_name=key_name,
                    bank=bank,
                    column=col,
                    address=addr,
                    old_config=old_cfg,
                    new_config=new_cfg,
                )
            )
    return diffs

