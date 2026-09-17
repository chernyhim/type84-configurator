"""
Host-side Profile Manager and Structured Profile Diff Engine.

Provides offline, software-defined profile management for IO by Red Square Type 84:
- Disk persistence for multiple profiles (JSON format with byte-exact hex data).
- Creation of isolated Profile instances from DeviceState snapshots.
- Structured subsystem-level diff engine (ProfileDiff) comparing:
  * Hall switch analog thresholds (actuation, RT press, RT release, flags)
  * Key Remap Layer 1 (Base layout)
  * Key Remap Layer 2 (Fn layout)
  * RGB Global lighting configuration
  * RGB Matrix / Per-Key buffer (512B)
  * Macro table buffer (400B)
  * DKS data table (1024B)
  * Game Mode performance settings (reported as separate device-global settings)
- ZERO physical writes are performed (passive/offline only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from keyboard_re.models.base import KEY_MAP
from keyboard_re.models.state import DeviceState, HallProfileState, Profile
from keyboard_re.protocol.keymap import KEYMAP_SLOT_COUNT, KeymapTable
from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    EFFECT_CUSTOM_READBACK_ALIASES,
    EFFECTS_WITH_FIXED_RAINBOW_READBACK,
    RGBGlobalConfig,
)


@dataclass
class KeyDiff:
    """Difference for an analog key configuration in the Hall matrix."""
    key_name: str
    parameter: str  # "actuation", "rt_press", "rt_release", "flags"
    old_value: Any
    new_value: Any
    address: Optional[int] = None


@dataclass
class SlotDiff:
    """Difference for a key binding slot in Remap Layer 1 or 2."""
    layer: int  # 1 or 2
    slot_index: int
    bank: int
    column: int
    old_hid_name: str
    new_hid_name: str
    old_scancode: int
    new_scancode: int
    old_type: int
    new_type: int


@dataclass
class ParamDiff:
    """Difference for a scalar configuration parameter."""
    parameter: str
    old_value: Any
    new_value: Any


@dataclass
class LedDiff:
    """Difference for a single LED in the per-key RGB matrix."""
    led_index: int
    old_color: Tuple[int, int, int]
    new_color: Tuple[int, int, int]


@dataclass
class SubsystemDiff:
    """Structured diff summary and details for an individual subsystem."""
    subsystem: str  # "hall", "remap_l1", "remap_l2", "rgb_global", "rgb_matrix", "macro_raw", "dks_raw", "game_mode"
    has_changes: bool
    change_count: int
    summary: str
    details: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subsystem": self.subsystem,
            "has_changes": self.has_changes,
            "change_count": self.change_count,
            "summary": self.summary,
            "details_count": len(self.details),
        }


@dataclass
class ProfileDiff:
    """
    Structured comparison result between a Profile and a DeviceState (or another Profile).
    Organized strictly by keyboard subsystems.
    """
    profile_id: int
    profile_name: str
    target_name: str  # e.g. "DeviceState" or target profile name
    subsystems: Dict[str, SubsystemDiff] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(sub.has_changes for sub in self.subsystems.values())

    @property
    def total_changes(self) -> int:
        return sum(sub.change_count for sub in self.subsystems.values())

    @property
    def changed_subsystems(self) -> List[str]:
        return [name for name, sub in self.subsystems.items() if sub.has_changes]

    def get_subsystem(self, name: str) -> Optional[SubsystemDiff]:
        return self.subsystems.get(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "target_name": self.target_name,
            "has_changes": self.has_changes,
            "total_changes": self.total_changes,
            "changed_subsystems": self.changed_subsystems,
            "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
        }

    def format_text(self) -> str:
        lines = [
            f"=== ProfileDiff: '{self.profile_name}' (ID {self.profile_id}) vs {self.target_name} ===",
            f"Total changes: {self.total_changes}",
        ]
        if not self.has_changes:
            lines.append("No differences found (identical).")
            return "\n".join(lines)

        lines.append(f"Changed subsystems: {', '.join(self.changed_subsystems)}")
        lines.append("-" * 60)
        for name, sub in self.subsystems.items():
            status_tag = f"[{name.upper()}]"
            if sub.has_changes:
                lines.append(f"{status_tag} {sub.summary}")
                for d in sub.details[:10]:
                    if isinstance(d, KeyDiff):
                        lines.append(f"  * Key '{d.key_name}': {d.parameter} = {d.old_value} -> {d.new_value}")
                    elif isinstance(d, SlotDiff):
                        lines.append(f"  * Slot {d.slot_index} (B{d.bank}, C{d.column}): {d.old_hid_name} -> {d.new_hid_name}")
                    elif isinstance(d, ParamDiff):
                        lines.append(f"  * {d.parameter}: {d.old_value} -> {d.new_value}")
                    elif isinstance(d, LedDiff):
                        lines.append(f"  * LED {d.led_index}: {d.old_color} -> {d.new_color}")
                    else:
                        lines.append(f"  * {d}")
                if len(sub.details) > 10:
                    lines.append(f"  * ... ({len(sub.details) - 10} more items)")
            else:
                lines.append(f"{status_tag} {sub.summary}")
        lines.append("-" * 60)
        return "\n".join(lines)


class ProfileManager:
    """
    Host-side Profile Manager for IO by Red Square Type 84 Magnetic Black.

    Responsibilities:
    - Manage profile persistence on disk (JSON format with byte-exact hex data).
    - Store multiple profiles in a specified directory.
    - Create Profile instances from live or captured DeviceState.
    - Compare Profile with DeviceState or other Profile instances (structured ProfileDiff).
    - ZERO physical write operations are executed.
    """

    def __init__(self, storage_dir: Union[str, Path] = "profiles"):
        self.storage_dir = Path(storage_dir)
        self._cache: Dict[int, Profile] = {}

    def ensure_dir(self) -> Path:
        """Ensure the profile storage directory exists on disk."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return self.storage_dir

    def save_profile(self, profile: Profile, filename: Optional[str] = None) -> Path:
        """
        Save a Profile instance to disk as a JSON file.
        Guarantees 100% byte-exact hex preservation of all subsystem buffers.
        """
        self.ensure_dir()
        if not filename:
            clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile.name.strip().lower())
            clean_name = clean_name or f"profile_{profile.profile_id}"
            filename = f"profile_{profile.profile_id}_{clean_name}.json"

        file_path = self.storage_dir / filename
        profile.save_json(file_path)
        self._cache[profile.profile_id] = profile
        return file_path

    def load_profile(self, target: Union[str, Path, int]) -> Profile:
        """
        Load a Profile from disk by file path, filename, or profile_id integer.
        """
        if isinstance(target, int):
            # First check cache
            if target in self._cache:
                return self._cache[target]

            # Search in storage_dir for a profile file matching target profile_id
            self.ensure_dir()
            for p_file in sorted(self.storage_dir.glob("*.json")):
                try:
                    p = Profile.load_json(p_file)
                    if p.profile_id == target:
                        self._cache[target] = p
                        return p
                except Exception:
                    continue
            raise FileNotFoundError(f"No profile with profile_id={target} found in {self.storage_dir}")

        path = Path(target)
        if not path.is_file() and not path.is_absolute():
            # Try relative to storage_dir
            candidate = self.storage_dir / path
            if candidate.is_file():
                path = candidate

        if not path.is_file():
            raise FileNotFoundError(f"Profile file '{target}' does not exist")

        profile = Profile.load_json(path)
        self._cache[profile.profile_id] = profile
        return profile

    def get_profile(self, profile_id: int) -> Profile:
        """Convenience alias for load_profile by ID."""
        return self.load_profile(profile_id)

    def list_profiles(self) -> List[Dict[str, Any]]:
        """
        Scan storage directory and return metadata summaries for all saved profiles.
        """
        self.ensure_dir()
        summaries: List[Dict[str, Any]] = []
        for p_file in sorted(self.storage_dir.glob("*.json")):
            try:
                p = Profile.load_json(p_file)
                summaries.append({
                    "profile_id": p.profile_id,
                    "name": p.name,
                    "file_path": str(p_file),
                    "file_name": p_file.name,
                    "has_hall": p.hall is not None,
                    "has_remap_l1": p.remap is not None,
                    "has_remap_l2": p.remap_l2 is not None,
                    "has_rgb_global": p.rgb_global is not None,
                    "has_rgb_matrix": p.rgb_matrix is not None,
                    "has_macros": p.macros_raw is not None,
                    "has_dks": p.dks_raw is not None,
                })
            except Exception:
                continue
        return summaries

    def delete_profile(self, target: Union[str, Path, int]) -> bool:
        """
        Delete a profile file from disk and remove from memory cache.
        """
        if isinstance(target, int):
            self.ensure_dir()
            deleted = False
            for p_file in sorted(self.storage_dir.glob("*.json")):
                try:
                    p = Profile.load_json(p_file)
                    if p.profile_id == target:
                        p_file.unlink(missing_ok=True)
                        deleted = True
                except Exception:
                    continue
            self._cache.pop(target, None)
            return deleted

        path = Path(target)
        if not path.is_file() and not path.is_absolute():
            path = self.storage_dir / path

        if path.is_file():
            try:
                p = Profile.load_json(path)
                self._cache.pop(p.profile_id, None)
            except Exception:
                pass
            path.unlink(missing_ok=True)
            return True
        return False

    def create_profile_from_state(
        self,
        state: DeviceState,
        profile_id: int = 1,
        name: str = "",
    ) -> Profile:
        """
        Create a new standalone Profile extracted and cloned from a DeviceState instance.
        """
        return state.create_profile(profile_id=profile_id, name=name)

    def compare_profile_with_state(
        self,
        profile: Profile,
        state: DeviceState,
        is_readback: bool = False,
    ) -> ProfileDiff:
        """
        Compare a Profile with a DeviceState across all 7 profile subsystems.
        Game Mode is noted as separate device-global settings.
        """
        subsystems: Dict[str, SubsystemDiff] = {}

        # Baseline: state (current device). Target: profile (candidate).
        # 1. Hall Switch Thresholds
        subsystems["hall"] = self._diff_hall(state.hall_profile_1, profile.hall)

        # 2. Remap Layer 1
        subsystems["remap_l1"] = self._diff_remap(state.remap_l1, profile.remap, layer=1)

        # 3. Remap Layer 2 (Fn)
        subsystems["remap_l2"] = self._diff_remap(state.remap_l2, profile.remap_l2, layer=2)

        # 4. RGB Global
        subsystems["rgb_global"] = self._diff_rgb_global(
            state.rgb_global,
            profile.rgb_global,
            is_readback=is_readback,
        )

        # 5. RGB Matrix
        subsystems["rgb_matrix"] = self._diff_rgb_matrix(state.rgb_matrix_raw, profile.rgb_matrix)

        # 6. Macro Raw
        subsystems["macro_raw"] = self._diff_macro_raw(state.macro_table_raw, profile.macros_raw)

        # 7. DKS Raw
        subsystems["dks_raw"] = self._diff_dks_raw(state.dks_table_raw, profile.dks_raw)

        # 8. Game Mode / Settings (AA 11 / AA 21)
        subsystems["game_mode"] = self._diff_game_mode(state.game_mode, profile.game_mode)

        return ProfileDiff(
            profile_id=profile.profile_id,
            profile_name=profile.name or f"Profile {profile.profile_id}",
            target_name="DeviceState",
            subsystems=subsystems,
        )

    def compare_profiles(
        self,
        profile_a: Profile,
        profile_b: Profile,
    ) -> ProfileDiff:
        """
        Compare two Profile instances across all subsystems.
        """
        subsystems: Dict[str, SubsystemDiff] = {}

        subsystems["hall"] = self._diff_hall(profile_a.hall, profile_b.hall)
        subsystems["remap_l1"] = self._diff_remap(profile_a.remap, profile_b.remap, layer=1)
        subsystems["remap_l2"] = self._diff_remap(profile_a.remap_l2, profile_b.remap_l2, layer=2)
        subsystems["rgb_global"] = self._diff_rgb_global(profile_a.rgb_global, profile_b.rgb_global)
        subsystems["rgb_matrix"] = self._diff_rgb_matrix(profile_a.rgb_matrix, profile_b.rgb_matrix)
        subsystems["macro_raw"] = self._diff_macro_raw(profile_a.macros_raw, profile_b.macros_raw)
        subsystems["dks_raw"] = self._diff_dks_raw(profile_a.dks_raw, profile_b.dks_raw)
        subsystems["game_mode"] = self._diff_game_mode(profile_a.game_mode, profile_b.game_mode)

        return ProfileDiff(
            profile_id=profile_a.profile_id,
            profile_name=profile_a.name or f"Profile {profile_a.profile_id}",
            target_name=profile_b.name or f"Profile {profile_b.profile_id}",
            subsystems=subsystems,
        )

    def create_write_plan(
        self,
        state: DeviceState,
        profile: Profile,
    ) -> Any:
        """
        Create a deterministic, dry-run ProfileWritePlan for applying a Profile to DeviceState.
        ZERO physical write operations are executed.
        """
        from keyboard_re.protocol.plan import build_profile_write_plan
        return build_profile_write_plan(state, profile)

    def apply_profile(
        self,
        transport: Any,
        profile: Profile,
        **kwargs: Any,
    ) -> Any:
        """
        Apply a Profile to a connected keyboard device via the public applicator facade.
        """
        from keyboard_re.applicator import apply_profile
        return apply_profile(transport, profile, profile_manager=self, **kwargs)

    def update_profile_rgb(
        self,
        profile_id: int,
        config_or_editor: Any,
        save: bool = True,
    ) -> Profile:
        """
        Update the Global RGB configuration of a managed profile.
        
        Accepts either an RGBGlobalConfig or an RGBGlobalEditor.
        Optionally persists the updated profile to disk if save=True.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Profile with ID {profile_id} not found in ProfileManager")

        profile.set_rgb_global(config_or_editor)
        if save:
            self.save_profile(profile)
        return profile

    def update_profile_rgb_matrix(
        self,
        profile_id: int,
        matrix: Any,
        save: bool = True,
    ) -> Profile:
        """
        Update the 512-byte Per-Key RGB matrix buffer of a managed profile.
        
        Accepts either an RGBMatrix instance or 512 raw bytes.
        Optionally persists to disk immediately.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Profile with ID {profile_id} not found in ProfileManager")

        profile.set_rgb_matrix(matrix)
        if save:
            self.save_profile(profile)
        return profile

    # -------------------------------------------------------------------------
    # Internal Subsystem Differs
    # -------------------------------------------------------------------------

    def _diff_hall(
        self,
        hall_a: Optional[HallProfileState],
        hall_b: Optional[HallProfileState],
    ) -> SubsystemDiff:
        if hall_a is None and hall_b is None:
            return SubsystemDiff("hall", False, 0, "Both absent", [])
        if hall_a is None or hall_b is None:
            return SubsystemDiff("hall", True, 1, "Presence mismatch (one absent)", [])

        diffs: List[KeyDiff] = []
        all_keys = sorted(list(set(hall_a.keys.keys()) | set(hall_b.keys.keys())))

        for k in all_keys:
            if k not in hall_a.keys or k not in hall_b.keys:
                diffs.append(KeyDiff(k, "presence", k in hall_a.keys, k in hall_b.keys))
                continue

            cfg_a = hall_a.keys[k]
            cfg_b = hall_b.keys[k]

            if abs(cfg_a.actuation_mm - cfg_b.actuation_mm) >= 0.005:
                diffs.append(
                    KeyDiff(
                        key_name=k,
                        parameter="actuation",
                        old_value=f"{cfg_a.actuation_mm:.2f} mm",
                        new_value=f"{cfg_b.actuation_mm:.2f} mm",
                    )
                )

            if abs(cfg_a.rt_press_mm - cfg_b.rt_press_mm) >= 0.005:
                diffs.append(
                    KeyDiff(
                        key_name=k,
                        parameter="rt_press",
                        old_value=f"{cfg_a.rt_press_mm:.2f} mm",
                        new_value=f"{cfg_b.rt_press_mm:.2f} mm",
                    )
                )

            if abs(cfg_a.rt_release_mm - cfg_b.rt_release_mm) >= 0.005:
                diffs.append(
                    KeyDiff(
                        key_name=k,
                        parameter="rt_release",
                        old_value=f"{cfg_a.rt_release_mm:.2f} mm",
                        new_value=f"{cfg_b.rt_release_mm:.2f} mm",
                    )
                )

            if cfg_a.flags != cfg_b.flags:
                diffs.append(
                    KeyDiff(
                        key_name=k,
                        parameter="flags",
                        old_value=f"0x{cfg_a.flags:04X}",
                        new_value=f"0x{cfg_b.flags:04X}",
                    )
                )

        has_changes = len(diffs) > 0
        changed_keys_count = len(set(d.key_name for d in diffs))
        summary = (
            f"{len(diffs)} parameter(s) modified across {changed_keys_count} key(s)"
            if has_changes
            else "Identical"
        )
        return SubsystemDiff("hall", has_changes, len(diffs), summary, diffs)

    def _diff_remap(
        self,
        remap_a: Optional[KeymapTable],
        remap_b: Optional[KeymapTable],
        layer: int,
    ) -> SubsystemDiff:
        sub_name = f"remap_l{layer}"
        if remap_a is None and remap_b is None:
            return SubsystemDiff(sub_name, False, 0, "Both absent", [])
        if remap_a is None or remap_b is None:
            return SubsystemDiff(sub_name, True, 1, "Presence mismatch (one absent)", [])

        diffs: List[SlotDiff] = []
        for s in range(KEYMAP_SLOT_COUNT):
            rec_a = remap_a.slots.get(s)
            rec_b = remap_b.slots.get(s)
            if rec_a is None or rec_b is None:
                continue

            if rec_a.to_bytes() != rec_b.to_bytes():
                diffs.append(
                    SlotDiff(
                        layer=layer,
                        slot_index=s,
                        bank=s // 16,
                        column=s % 16,
                        old_hid_name=rec_a.hid_name,
                        new_hid_name=rec_b.hid_name,
                        old_scancode=rec_a.scancode,
                        new_scancode=rec_b.scancode,
                        old_type=rec_a.function_type,
                        new_type=rec_b.function_type,
                    )
                )

        has_changes = len(diffs) > 0
        summary = f"{len(diffs)} slot(s) modified" if has_changes else "Identical"
        return SubsystemDiff(sub_name, has_changes, len(diffs), summary, diffs)

    def _diff_rgb_global(
        self,
        rgb_a: Optional[RGBGlobalConfig],
        rgb_b: Optional[RGBGlobalConfig],
        is_readback: bool = False,
    ) -> SubsystemDiff:
        if rgb_a is None and rgb_b is None:
            return SubsystemDiff("rgb_global", False, 0, "Both absent", [])
        if rgb_a is None or rgb_b is None:
            return SubsystemDiff("rgb_global", True, 1, "Presence mismatch", [])

        diffs: List[ParamDiff] = []

        # Check Custom Mode readback equivalence:
        # When target is Custom Mode (EFFECT_CUSTOM = 0x80), the firmware readback
        # status register (AA 13 -> 55 13) returns 0x14 (or 0x15 on some revisions)
        # as a runtime active-mode status code. Furthermore, per-key colors reside
        # entirely in the 512-byte matrix (AA 24), so firmware readback reports
        # default #FFFFFF for primary color and color_mode 1. These runtime status
        # fields are equivalent for Custom Mode and not considered mismatches.
        is_custom_equiv = (
            (rgb_a.effect in EFFECT_CUSTOM_READBACK_ALIASES and rgb_b.effect in EFFECT_CUSTOM_READBACK_ALIASES)
            and (rgb_a.effect == EFFECT_CUSTOM or rgb_b.effect == EFFECT_CUSTOM)
        )

        # Check Ripple Spread (Effect 0x0F) firmware readback quirk:
        # For effect 0x0F (EFFECT_RIPPLE_SPREAD), keyboard firmware physically ignores
        # primary_color and color_mode on AA 23 write and always returns in AA 13 readback:
        # primary_color = (255, 255, 255) (#FFFFFF) and color_mode = 1 (Rainbow RGB).
        # During readback verification, primary_color and color_mode are not considered mismatches.
        is_ripple_readback = (
            is_readback
            and (rgb_a.effect in EFFECTS_WITH_FIXED_RAINBOW_READBACK and rgb_b.effect in EFFECTS_WITH_FIXED_RAINBOW_READBACK)
        )

        if not is_custom_equiv and rgb_a.effect != rgb_b.effect:
            diffs.append(ParamDiff("effect", rgb_a.effect, rgb_b.effect))

        if is_custom_equiv:
            # In Custom Mode, per-key colors reside in the 512B matrix (AA 24).
            # Hardware readback in AA 13 retains default #FFFFFF.
            if (
                rgb_a.primary != rgb_b.primary
                and rgb_a.primary != (255, 255, 255)
                and rgb_b.primary != (255, 255, 255)
            ):
                diffs.append(ParamDiff("primary_color", rgb_a.primary, rgb_b.primary))
        elif is_ripple_readback:
            # Effect 0x0F quirk: firmware always returns (255, 255, 255) in AA 13 readback
            if (
                rgb_a.primary != rgb_b.primary
                and rgb_a.primary != (255, 255, 255)
                and rgb_b.primary != (255, 255, 255)
            ):
                diffs.append(ParamDiff("primary_color", rgb_a.primary, rgb_b.primary))
        elif rgb_a.primary != rgb_b.primary:
            diffs.append(ParamDiff("primary_color", rgb_a.primary, rgb_b.primary))

        if rgb_a.secondary != rgb_b.secondary:
            diffs.append(ParamDiff("secondary_color", rgb_a.secondary, rgb_b.secondary))

        if is_custom_equiv:
            # In Custom Mode, color_mode 0 (single color) vs 1 (runtime status) is equivalent.
            if {rgb_a.color_mode, rgb_b.color_mode} - {0, 1}:
                diffs.append(ParamDiff("color_mode", rgb_a.color_mode, rgb_b.color_mode))
        elif is_ripple_readback:
            # Effect 0x0F quirk: firmware always returns color_mode 1 in AA 13 readback
            if {rgb_a.color_mode, rgb_b.color_mode} - {0, 1}:
                diffs.append(ParamDiff("color_mode", rgb_a.color_mode, rgb_b.color_mode))
        elif rgb_a.color_mode != rgb_b.color_mode:
            diffs.append(ParamDiff("color_mode", rgb_a.color_mode, rgb_b.color_mode))

        # Brightness and speed remain strictly verified across all modes
        if rgb_a.brightness != rgb_b.brightness:
            diffs.append(ParamDiff("brightness", rgb_a.brightness, rgb_b.brightness))
        if rgb_a.speed != rgb_b.speed:
            diffs.append(ParamDiff("speed", rgb_a.speed, rgb_b.speed))
        if rgb_a.direction != rgb_b.direction:
            diffs.append(ParamDiff("direction", rgb_a.direction, rgb_b.direction))
        if rgb_a.effect_mode_type != rgb_b.effect_mode_type:
            diffs.append(ParamDiff("effect_mode_type", rgb_a.effect_mode_type, rgb_b.effect_mode_type))
        # driver_setting is a host-write transport command byte (hardcoded 255 on AA 23),
        # whereas hardware readback (AA 13 -> 55 13) returns 0 (internal runtime status).
        # It is not a user-configurable parameter and must not trigger diff mismatches.

        has_changes = len(diffs) > 0
        summary = f"{len(diffs)} parameter(s) modified" if has_changes else "Identical"
        return SubsystemDiff("rgb_global", has_changes, len(diffs), summary, diffs)

    def _diff_rgb_matrix(
        self,
        matrix_a: Optional[bytes],
        matrix_b: Optional[bytes],
    ) -> SubsystemDiff:
        if matrix_a is None and matrix_b is None:
            return SubsystemDiff("rgb_matrix", False, 0, "Both absent", [])
        if matrix_a is None or matrix_b is None:
            return SubsystemDiff("rgb_matrix", True, 1, "Presence mismatch", [])

        diffs: List[LedDiff] = []
        if matrix_a != matrix_b:
            for i in range(128):
                off = i * 4
                if off + 3 <= len(matrix_a) and off + 3 <= len(matrix_b):
                    col_a = (matrix_a[off], matrix_a[off + 1], matrix_a[off + 2])
                    col_b = (matrix_b[off], matrix_b[off + 1], matrix_b[off + 2])
                    if col_a != col_b:
                        diffs.append(LedDiff(led_index=i, old_color=col_a, new_color=col_b))

        has_changes = len(diffs) > 0 or len(matrix_a) != len(matrix_b)
        change_count = len(diffs) if diffs else (1 if has_changes else 0)
        summary = f"{len(diffs)} LED color(s) modified" if diffs else ("Identical" if not has_changes else "Size mismatch")
        return SubsystemDiff("rgb_matrix", has_changes, change_count, summary, diffs)

    def _diff_macro_raw(
        self,
        macro_a: Optional[bytes],
        macro_b: Optional[bytes],
    ) -> SubsystemDiff:
        if macro_a is None and macro_b is None:
            return SubsystemDiff("macro_raw", False, 0, "Both absent", [])
        if macro_a is None or macro_b is None:
            return SubsystemDiff("macro_raw", True, 1, "Presence mismatch", [])

        if macro_a == macro_b:
            return SubsystemDiff("macro_raw", False, 0, "Identical", [])

        diff_count = sum(1 for a, b in zip(macro_a, macro_b) if a != b) + abs(len(macro_a) - len(macro_b))
        summary = f"{diff_count} byte(s) modified in 400B macro buffer"
        return SubsystemDiff("macro_raw", True, diff_count, summary, [ParamDiff("macro_raw_bytes", len(macro_a), len(macro_b))])

    def _diff_dks_raw(
        self,
        dks_a: Optional[bytes],
        dks_b: Optional[bytes],
    ) -> SubsystemDiff:
        if dks_a is None and dks_b is None:
            return SubsystemDiff("dks_raw", False, 0, "Both absent", [])
        if dks_a is None or dks_b is None:
            return SubsystemDiff("dks_raw", True, 1, "Presence mismatch", [])

        if dks_a == dks_b:
            return SubsystemDiff("dks_raw", False, 0, "Identical", [])

        diff_count = sum(1 for a, b in zip(dks_a, dks_b) if a != b) + abs(len(dks_a) - len(dks_b))
        changed_records = [
            r for r in range(min(len(dks_a), len(dks_b)) // 16)
            if dks_a[r * 16 : (r + 1) * 16] != dks_b[r * 16 : (r + 1) * 16]
        ]
        summary = f"{diff_count} byte(s) modified across {len(changed_records)} DKS record(s)"
        return SubsystemDiff(
            "dks_raw",
            True,
            diff_count,
            summary,
            [ParamDiff("dks_changed_slots", None, changed_records)],
        )

    def _diff_game_mode(
        self,
        gm_a: Optional[Any],
        gm_b: Optional[Any],
    ) -> SubsystemDiff:
        if gm_a is None and gm_b is None:
            return SubsystemDiff("game_mode", False, 0, "Both absent", [])
        if gm_a is None or gm_b is None:
            return SubsystemDiff("game_mode", True, 1, "Presence mismatch", [])

        diffs: List[ParamDiff] = []
        fields_to_compare = [
            ("stability_mode", "Stability Mode"),
            ("auto_calibration", "Auto Calibration"),
            ("report_rate", "Report Rate"),
            ("sleep_time", "Sleep Time"),
            ("game_mode", "Game Mode"),
            ("fn_switch", "Fn Switch"),
            ("key_delay", "Key Delay"),
            ("system_mode", "System Mode"),
            ("top_deadzone", "Top Deadzone"),
            ("bottom_deadzone", "Bottom Deadzone"),
            ("single_key_wakeup", "Single Key Wakeup"),
            ("push_button_mode", "Push Button Mode"),
        ]

        for attr, name in fields_to_compare:
            val_a = getattr(gm_a, attr, None)
            val_b = getattr(gm_b, attr, None)
            if val_a != val_b:
                diffs.append(ParamDiff(name, val_a, val_b))

        has_changes = len(diffs) > 0
        summary = f"{len(diffs)} setting(s) modified (device-global)" if diffs else "Identical (device-global)"
        return SubsystemDiff(
            subsystem="game_mode",
            has_changes=has_changes,
            change_count=len(diffs),
            summary=summary,
            details=diffs,
        )

