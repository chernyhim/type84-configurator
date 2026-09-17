"""
Universal Structured Diff Engine for Keyboard State Snapshots.

Computes semantic and byte-level diffs across all keyboard subsystems:
- Hall Effect analog thresholds & Rapid Trigger settings
- Key Remapping matrices (Layer 1 Base, Layer 2 Fn, Layer 3 Alt)
- Global and Per-Key RGB lighting
- Macro / DKS tables
- Device Status & Active Profile
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from keyboard_re.models.state import KeyboardSnapshot


@dataclass
class StateDiff:
    """
    Detailed atomic difference between two keyboard states.
    """
    subsystem: str  # "hall", "rgb_global", "rgb_per_key", "keymap_l1", "keymap_l2", "keymap_l3", "macro", "status"
    target: str     # e.g. "Key A", "Global RGB", "Slot 50", "Active Profile"
    parameter: str  # e.g. "actuation", "rt_press", "effect", "scancode"
    old_value: Any
    new_value: Any
    address: Optional[int] = None
    old_bytes: Optional[bytes] = None
    new_bytes: Optional[bytes] = None
    profile_id: Optional[int] = None

    def format_text(self) -> str:
        addr_str = f" [Addr 0x{self.address:04X}]" if self.address is not None else ""
        prof_str = f" [Profile {self.profile_id}]" if self.profile_id is not None else ""
        bytes_str = ""
        if self.old_bytes is not None and self.new_bytes is not None:
            bytes_str = f" (raw: {self.old_bytes.hex(' ').upper()} -> {self.new_bytes.hex(' ').upper()})"

        return (
            f"[{self.subsystem.upper()}]{prof_str} {self.target}{addr_str}: "
            f"{self.parameter} = {self.old_value} -> {self.new_value}{bytes_str}"
        )


def compare_snapshots(before: KeyboardSnapshot, after: KeyboardSnapshot) -> List[StateDiff]:
    """
    Compare two KeyboardSnapshots and return a list of StateDiff records.
    """
    diffs: List[StateDiff] = []

    # 1. Status & Active Profile
    if before.active_profile_id != after.active_profile_id:
        diffs.append(
            StateDiff(
                subsystem="status",
                target="Keyboard State",
                parameter="active_profile",
                old_value=before.active_profile_id,
                new_value=after.active_profile_id,
                address=11,
            )
        )

    # 2. Profiles comparison (Hall, Keymap, RGB, Macros)
    all_profile_ids = sorted(list(set(before.profiles.keys()) | set(after.profiles.keys())))
    for pid in all_profile_ids:
        if pid not in before.profiles or pid not in after.profiles:
            diffs.append(
                StateDiff(
                    subsystem="profile",
                    target=f"Profile {pid}",
                    parameter="presence",
                    old_value=pid in before.profiles,
                    new_value=pid in after.profiles,
                    profile_id=pid,
                )
            )
            continue

        p_before = before.profiles[pid]
        p_after = after.profiles[pid]

        # 2a. Hall Matrix
        for key_name in p_before.hall.keys:
            cfg_b = p_before.hall.get_key(key_name)
            cfg_a = p_after.hall.get_key(key_name)
            if cfg_b != cfg_a:
                # Calculate key address in 1008-byte image
                from keyboard_re.models.base import KEY_MAP, key_address
                bank, col = KEY_MAP[key_name]
                k_addr = key_address(bank, col)

                if cfg_b.actuation != cfg_a.actuation:
                    diffs.append(
                        StateDiff(
                            subsystem="hall",
                            target=f"Key {key_name}",
                            parameter="actuation",
                            old_value=f"{cfg_b.actuation_mm:.2f} mm",
                            new_value=f"{cfg_a.actuation_mm:.2f} mm",
                            address=k_addr + 2,
                            old_bytes=cfg_b.to_bytes()[2:4],
                            new_bytes=cfg_a.to_bytes()[2:4],
                            profile_id=pid,
                        )
                    )
                if cfg_b.rt_press != cfg_a.rt_press:
                    diffs.append(
                        StateDiff(
                            subsystem="hall",
                            target=f"Key {key_name}",
                            parameter="rt_press",
                            old_value=f"{cfg_b.rt_press_mm:.2f} mm",
                            new_value=f"{cfg_a.rt_press_mm:.2f} mm",
                            address=k_addr + 4,
                            old_bytes=cfg_b.to_bytes()[4:6],
                            new_bytes=cfg_a.to_bytes()[4:6],
                            profile_id=pid,
                        )
                    )
                if cfg_b.rt_release != cfg_a.rt_release:
                    diffs.append(
                        StateDiff(
                            subsystem="hall",
                            target=f"Key {key_name}",
                            parameter="rt_release",
                            old_value=f"{cfg_b.rt_release_mm:.2f} mm",
                            new_value=f"{cfg_a.rt_release_mm:.2f} mm",
                            address=k_addr + 6,
                            old_bytes=cfg_b.to_bytes()[6:8],
                            new_bytes=cfg_a.to_bytes()[6:8],
                            profile_id=pid,
                        )
                    )
                if cfg_b.flags != cfg_a.flags:
                    diffs.append(
                        StateDiff(
                            subsystem="hall",
                            target=f"Key {key_name}",
                            parameter="flags",
                            old_value=f"0x{cfg_b.flags:02X}",
                            new_value=f"0x{cfg_a.flags:02X}",
                            address=k_addr + 1,
                            old_bytes=cfg_b.to_bytes()[1:2],
                            new_bytes=cfg_a.to_bytes()[1:2],
                            profile_id=pid,
                        )
                    )

        # 2b. Keymap Layer 1 (Base)
        for slot_idx in range(len(p_before.keymap.slots)):
            rec_b = p_before.keymap.slots[slot_idx]
            rec_a = p_after.keymap.slots[slot_idx]
            if rec_b != rec_a:
                diffs.append(
                    StateDiff(
                        subsystem="keymap_l1",
                        target=f"Slot {slot_idx} (B{slot_idx//16}, C{slot_idx%16})",
                        parameter="binding",
                        old_value=f"{rec_b.hid_name} (type 0x{rec_b.function_type:02X})",
                        new_value=f"{rec_a.hid_name} (type 0x{rec_a.function_type:02X})",
                        address=slot_idx * 4,
                        old_bytes=rec_b.to_bytes(),
                        new_bytes=rec_a.to_bytes(),
                        profile_id=pid,
                    )
                )

        # 2c. Global RGB
        if p_before.rgb_global is not None and p_after.rgb_global is not None:
            rgb_b = p_before.rgb_global
            rgb_a = p_after.rgb_global
            if rgb_b.effect != rgb_a.effect:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="effect",
                        old_value=rgb_b.effect,
                        new_value=rgb_a.effect,
                        address=8,
                        profile_id=pid,
                    )
                )
            if rgb_b.primary != rgb_a.primary:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="primary_color",
                        old_value=rgb_b.primary,
                        new_value=rgb_a.primary,
                        address=9,
                        profile_id=pid,
                    )
                )
            if rgb_b.secondary != rgb_a.secondary:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="secondary_color",
                        old_value=rgb_b.secondary,
                        new_value=rgb_a.secondary,
                        address=13,
                        profile_id=pid,
                    )
                )
            if rgb_b.color_mode != rgb_a.color_mode:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="color_mode",
                        old_value=rgb_b.color_mode,
                        new_value=rgb_a.color_mode,
                        address=16,
                        profile_id=pid,
                    )
                )
            if rgb_b.brightness != rgb_a.brightness:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="brightness",
                        old_value=rgb_b.brightness,
                        new_value=rgb_a.brightness,
                        address=17,
                        profile_id=pid,
                    )
                )
            if rgb_b.speed != rgb_a.speed:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="speed",
                        old_value=rgb_b.speed,
                        new_value=rgb_a.speed,
                        address=18,
                        profile_id=pid,
                    )
                )
            if rgb_b.direction != rgb_a.direction:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="direction",
                        old_value=rgb_b.direction,
                        new_value=rgb_a.direction,
                        address=19,
                        profile_id=pid,
                    )
                )
            if rgb_b.effect_mode_type != rgb_a.effect_mode_type:
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="effect_mode_type",
                        old_value=rgb_b.effect_mode_type,
                        new_value=rgb_a.effect_mode_type,
                        address=20,
                        profile_id=pid,
                    )
                )
            if (
                rgb_b.driver_setting != rgb_a.driver_setting
                and {rgb_a.driver_setting, rgb_b.driver_setting} != {0, 255}
            ):
                diffs.append(
                    StateDiff(
                        subsystem="rgb_global",
                        target="Global Lighting",
                        parameter="driver_setting",
                        old_value=rgb_b.driver_setting,
                        new_value=rgb_a.driver_setting,
                        address=12,
                        profile_id=pid,
                    )
                )

        # 2d. Per-Key RGB
        if p_before.rgb_per_key is not None and p_after.rgb_per_key is not None:
            if p_before.rgb_per_key != p_after.rgb_per_key:
                for led_idx in range(128):
                    off = led_idx * 4
                    led_b = p_before.rgb_per_key[off : off + 3]
                    led_a = p_after.rgb_per_key[off : off + 3]
                    if led_b != led_a:
                        diffs.append(
                            StateDiff(
                                subsystem="rgb_per_key",
                                target=f"LED {led_idx}",
                                parameter="color",
                                old_value=tuple(led_b),
                                new_value=tuple(led_a),
                                address=off,
                                old_bytes=led_b,
                                new_bytes=led_a,
                                profile_id=pid,
                            )
                        )

    # 3. Fn Layer (Layer 2)
    if before.fn_layer is not None and after.fn_layer is not None:
        for slot_idx in range(len(before.fn_layer.slots)):
            rec_b = before.fn_layer.slots[slot_idx]
            rec_a = after.fn_layer.slots[slot_idx]
            if rec_b != rec_a:
                diffs.append(
                    StateDiff(
                        subsystem="keymap_l2",
                        target=f"Fn Slot {slot_idx} (B{slot_idx//16}, C{slot_idx%16})",
                        parameter="binding",
                        old_value=f"{rec_b.hid_name} (type 0x{rec_b.function_type:02X})",
                        new_value=f"{rec_a.hid_name} (type 0x{rec_a.function_type:02X})",
                        address=slot_idx * 4,
                        old_bytes=rec_b.to_bytes(),
                        new_bytes=rec_a.to_bytes(),
                    )
                )

    return diffs
