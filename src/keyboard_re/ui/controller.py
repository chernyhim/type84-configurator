"""
Application Controller for IO by Red Square Type 84 GUI.

Strict Architectural Isolation:
- Single integration boundary between UI widgets and the backend.
- UI views interact ONLY with AppController and domain models.
- NO direct imports of protocol AAxx opcodes, packet builders, chunkers, or raw HID writes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from keyboard_re.applicator import (
    ApplyProfileResult,
    ExecutionStatus,
    PlanConfirmationSummary,
    build_confirmation_summary,
)
from keyboard_re.models.base import REVERSE_KEY_MAP, KeyConfig, key_address
from keyboard_re.models.dks import (
    DEFAULT_BREAK_VALUE_1_MM,
    DEFAULT_BREAK_VALUE_2_MM,
    DEFAULT_MAKE_VALUE_1_MM,
    DEFAULT_MAKE_VALUE_2_MM,
    DKSEventState,
    DKSRecord,
    DKSTable,
)
from keyboard_re.models.rgb_matrix import RGBMatrix
from keyboard_re.models.state import DeviceState, Profile, collect_device_state
from keyboard_re.profile_manager import ProfileDiff, ProfileManager
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.macro import MacroAction, MacroCatalog, MacroDefinition
from keyboard_re.protocol.read import GameModeResponse
from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    RGB_EFFECT_CATALOG,
    RGBEffectMeta,
    RGBGlobalConfig,
)
from keyboard_re.protocol.transport import HidTransport, MockHidTransport
from keyboard_re.ui.layout_data import (
    FIRMWARE_FALLBACK_DEFAULTS,
    FN_DISABLED_SWITCH_SLOTS,
    FN_REMAP_SLOT,
    KEY_BY_ID,
    KEY_BY_LED_SLOT,
    KEY_BY_REMAP_SLOT,
    KEY_BY_SWITCH_SLOT,
    PHYSICAL_84_REMAP_SLOTS,
    PHYSICAL_84_SWITCH_SLOTS,
    TYPE84_LAYOUT,
    get_function_type_for_scancode,
)

PHYSICAL_84_LED_SLOTS: frozenset[int] = frozenset(k.led_slot for k in TYPE84_LAYOUT)


@dataclass
class KeyDKSInfo:
    """
    Status details of a physical key's Dynamic Keystroke (DKS) configuration.
    Evidence classification:
    - TIER 1 PHYSICALLY CONFIRMED: 16B record, 64 slots, Remap L1 prefix 0x08 link, travel thresholds,
      actions 1..4 bitmasks (0x01, 0x02, 0x04, 0x08), Points 0..3 state bytes (+12..+15) TAP/HOLD nibbles.
    - TIER 2 STRUCTURAL INFERENCE: actions 3..4 scancode slot positions.
    """
    key_id: str
    label: str
    switch_slot: int
    remap_slot: int
    is_active: bool
    dks_slot_index: Optional[int]
    make_value_1_mm: float
    make_value_2_mm: float
    break_value_1_mm: float
    break_value_2_mm: float
    actions: List[int]
    states: List[List[DKSEventState]]
    is_modified: bool


@dataclass
class KeyRemapInfo:
    """Status details of a physical key's remap binding."""
    key_id: str
    label: str
    switch_slot: int
    remap_slot: int
    default_name: str
    current_name: str
    is_modified: bool
    is_default: bool
    is_unbound: bool
    is_readonly: bool
    record: Optional[KeyRemapRecord] = None


@dataclass
class KeyHallInfo:
    """Status details for a single physical key's Hall / RT settings."""
    key_id: str
    label: str
    switch_slot: int
    actuation_mm: float
    rt_press_mm: float
    rt_release_mm: float
    is_rt_enabled: bool
    flags: int
    default_actuation_mm: float
    default_rt_press_mm: float
    default_rt_release_mm: float
    is_modified: bool


@dataclass
class MultiKeyHallInfo:
    """Aggregated status across multiple selected physical keys."""
    count: int
    key_labels: List[str]
    switch_slots: Set[int]
    actuation_mm: Optional[float]      # None if mixed state
    rt_press_mm: Optional[float]       # None if mixed state
    rt_release_mm: Optional[float]     # None if mixed state
    is_rt_enabled: Optional[bool]      # None if mixed state
    has_mixed_actuation: bool
    has_mixed_rt: bool
    is_any_modified: bool



class AppController:
    """
    State manager and business logic controller for the GUI.
    """

    def __init__(
        self,
        profile_manager: Optional[ProfileManager] = None,
        default_capture_path: Optional[Path] = None,
    ) -> None:
        self.profile_manager = profile_manager or ProfileManager()
        import sys
        base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
        self.default_capture_path = default_capture_path or (
            base_dir
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )

        # Device & Profile state
        self.transport: Optional[HidTransport] = None
        self.device_state: Optional[DeviceState] = None  # Read-only baseline from keyboard
        self.working_profile: Optional[Profile] = None   # Mutable candidate edited in UI
        self.last_diff: Optional[ProfileDiff] = None
        self.last_apply_result: Optional[ApplyProfileResult] = None

        # UI State
        self.is_connected: bool = False
        self.is_mock: bool = False
        self.connection_status_text: str = "Disconnected"
        self.selected_key_slot: Optional[int] = None
        self.selected_led_slots: set[int] = set()
        self.primary_selected_led: Optional[int] = None
        self.active_tab: str = "rgb_global"
        self.is_dirty: bool = False
        self.diff_count: int = 0
        self.active_remap_layer: int = 1  # 1 = Base Layer (L1), 2 = Fn Layer (L2)

        # Change subscribers
        self._listeners: List[Callable[[], None]] = []

    def set_active_remap_layer(self, layer: int) -> None:
        """Switch active remap layer (1 for Base Layer, 2 for Fn Layer)."""
        if layer in (1, 2) and layer != self.active_remap_layer:
            self.active_remap_layer = layer
            self._notify()

    # -------------------------------------------------------------------------
    # Event subscription
    # -------------------------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified on any state change."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        """Unregister a state change callback."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        """Notify all registered listeners of a state change."""
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Connection Lifecycle
    # -------------------------------------------------------------------------

    def connect(
        self,
        use_mock: bool = False,
        capture_path: Optional[Path] = None,
        custom_transport: Optional[HidTransport] = None,
    ) -> bool:
        """
        Connect to a physical keyboard or initialize a mock session for offline dev/tests.
        """
        if use_mock:
            self.is_mock = True
            c_path = capture_path or self.default_capture_path
            if c_path.is_file():
                self.device_state = DeviceState.load_json(c_path)
            else:
                self.device_state = DeviceState()

            self.transport = custom_transport or MockHidTransport()
            self.connection_status_text = "Connected (Mock) [0x0C45:0x80D6]"
            self.is_connected = True
        else:
            # Physical hardware connection
            try:
                from keyboard_re.transport.native_hid import NativeHidTransport
                self.transport = custom_transport or NativeHidTransport()
                self.transport.open()
                self.device_state = collect_device_state(self.transport)
                self.is_mock = False
                self.connection_status_text = "Connected (Physical) [0x0C45:0x80D6]"
                self.is_connected = True
            except Exception as ex:
                self.is_connected = False
                self.connection_status_text = f"Connection Failed: {ex}"
                self._notify()
                return False

        # Create working profile clone from baseline
        self.working_profile = self.profile_manager.create_profile_from_state(
            self.device_state,
            profile_id=1,
            name="Working Profile",
        )

        self.recalculate_diff()
        self._notify()
        return True

    def disconnect(self) -> None:
        """Disconnect and reset active session."""
        if self.transport and hasattr(self.transport, "close"):
            try:
                self.transport.close()
            except Exception:
                pass

        self.transport = None
        self.device_state = None
        self.working_profile = None
        self.is_connected = False
        self.is_mock = False
        self.is_dirty = False
        self.diff_count = 0
        self.last_diff = None
        self.selected_key_slot = None
        self.selected_led_slots.clear()
        self.primary_selected_led = None
        self.connection_status_text = "Disconnected"
        self._notify()

    def discard_all_changes(self) -> None:
        """
        Discard all unsaved edits across all subsystems.
        Re-clones working_profile from current baseline device_state.
        Resets dirty flag and notifies all UI views.
        """
        if not self.device_state:
            return
        self.working_profile = self.profile_manager.create_profile_from_state(
            self.device_state,
            profile_id=self.working_profile.profile_id if self.working_profile else 1,
            name=self.working_profile.name if self.working_profile else "Working Profile",
        )
        self.recalculate_diff()
        self._notify()

    def save_profile_to_disk(self, target_path: Union[str, Path]) -> Path:
        """
        Save the current working profile to a JSON file on disk.
        Returns the resolved Path of the saved file.
        """
        if not self.working_profile:
            raise RuntimeError("Cannot save profile: no active working profile")
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.working_profile.save_json(path)
        return path

    def load_profile_from_disk(self, target_path: Union[str, Path]) -> Profile:
        """
        Load a Profile from a JSON file on disk into the working profile.
        Recalculates diff against current device_state and updates UI.
        """
        path = Path(target_path)
        if not path.is_file():
            raise FileNotFoundError(f"Profile file not found: {path}")
        loaded = Profile.load_json(path)
        self.working_profile = loaded
        self.recalculate_diff()
        self._notify()
        return loaded

    def list_saved_profiles(self) -> List[Dict[str, Any]]:
        """List metadata summaries for all profiles stored in profile_manager directory."""
        return self.profile_manager.list_profiles()

    # -------------------------------------------------------------------------
    # Selection & Navigation
    # -------------------------------------------------------------------------

    def select_key(self, switch_slot: Optional[int]) -> None:
        """Select a key in the visual keyboard view by switch slot (Remap/Hall)."""
        self.selected_key_slot = switch_slot
        if switch_slot is not None and switch_slot in KEY_BY_SWITCH_SLOT:
            led_s = KEY_BY_SWITCH_SLOT[switch_slot].led_slot
            self.selected_led_slots = {led_s}
            self.primary_selected_led = led_s
        elif switch_slot is None:
            self.selected_led_slots.clear()
            self.primary_selected_led = None
        self._notify()

    def select_led(
        self,
        led_slot: Optional[int],
        multi: bool = False,
        toggle: bool = False,
    ) -> None:
        """
        Select a key by LED slot (Per-Key RGB).
        Strictly enforces that led_slot belongs to the 84 physical keys.
        """
        if led_slot is None:
            self.deselect_all_leds()
            return

        if led_slot not in PHYSICAL_84_LED_SLOTS:
            # Strictly ignore non-physical / reserved LED slots
            return

        if toggle:
            if led_slot in self.selected_led_slots:
                self.selected_led_slots.remove(led_slot)
                if self.primary_selected_led == led_slot:
                    self.primary_selected_led = next(iter(self.selected_led_slots), None)
            else:
                self.selected_led_slots.add(led_slot)
                self.primary_selected_led = led_slot
        elif multi:
            self.selected_led_slots.add(led_slot)
            self.primary_selected_led = led_slot
        else:
            self.selected_led_slots = {led_slot}
            self.primary_selected_led = led_slot

        # Sync switch slot for identity inspection
        if self.primary_selected_led is not None and self.primary_selected_led in KEY_BY_LED_SLOT:
            self.selected_key_slot = KEY_BY_LED_SLOT[self.primary_selected_led].switch_slot
        else:
            self.selected_key_slot = None

        self._notify()

    def set_led_selection(self, slots: Set[int]) -> None:
        """
        Replace selection with the provided set of LED slots (drag selection).
        Strictly bounded to PHYSICAL_84_LED_SLOTS.
        """
        valid_slots = set(slots) & PHYSICAL_84_LED_SLOTS
        if not valid_slots:
            return

        self.selected_led_slots = valid_slots
        self.primary_selected_led = next(iter(valid_slots))
        if self.primary_selected_led in KEY_BY_LED_SLOT:
            self.selected_key_slot = KEY_BY_LED_SLOT[self.primary_selected_led].switch_slot
        self._notify()

    def add_leds_to_selection(self, slots: Set[int]) -> None:
        """
        Add the provided set of LED slots to the current selection (Shift + drag).
        Strictly bounded to PHYSICAL_84_LED_SLOTS.
        """
        valid_slots = set(slots) & PHYSICAL_84_LED_SLOTS
        if not valid_slots:
            return

        self.selected_led_slots.update(valid_slots)
        self.primary_selected_led = next(iter(valid_slots))
        if self.primary_selected_led in KEY_BY_LED_SLOT:
            self.selected_key_slot = KEY_BY_LED_SLOT[self.primary_selected_led].switch_slot
        self._notify()

    def select_all_leds(self) -> None:
        """Select all 84 physical LED slots in TYPE84_LAYOUT."""
        self.selected_led_slots = set(PHYSICAL_84_LED_SLOTS)
        self.primary_selected_led = TYPE84_LAYOUT[0].led_slot if TYPE84_LAYOUT else None
        if self.primary_selected_led in KEY_BY_LED_SLOT:
            self.selected_key_slot = KEY_BY_LED_SLOT[self.primary_selected_led].switch_slot
        self._notify()

    def deselect_all_leds(self) -> None:
        """Deselect all LED slots."""
        self.selected_led_slots.clear()
        self.primary_selected_led = None
        self.selected_key_slot = None
        self._notify()

    def select_led_group(self, group_name: str) -> None:
        """
        Select predefined groups of physical keys (WASD, Arrows, Modifiers, etc.).
        Operates strictly on the 84 physical keys.
        """
        group_keys: Dict[str, List[str]] = {
            "wasd": ["W", "A", "S", "D"],
            "arrows": ["UP", "DOWN", "LEFT", "RIGHT"],
            "modifiers": ["LCTRL", "LWIN", "LALT", "SPACE", "RALT", "FN", "RCTRL", "LSHIFT", "RSHIFT"],
            "nav": ["PRTSC", "INS", "DEL", "HOME", "END", "PGUP", "PGDN"],
            "numbers": ["GRAVE", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "MINUS", "EQUAL", "BACKSPACE"],
            "fkeys": ["ESC", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
        }
        target_ids = group_keys.get(group_name.lower(), [])
        slots: Set[int] = set()
        for kid in target_ids:
            if kid in KEY_BY_ID:
                slots.add(KEY_BY_ID[kid].led_slot)

        if slots:
            self.selected_led_slots = slots
            self.primary_selected_led = next(iter(slots))
            if self.primary_selected_led in KEY_BY_LED_SLOT:
                self.selected_key_slot = KEY_BY_LED_SLOT[self.primary_selected_led].switch_slot
            self._notify()

    @property
    def selected_switch_slots(self) -> Set[int]:
        """Return the set of physical switch slots currently selected."""
        slots: Set[int] = set()
        for s in self.selected_led_slots:
            if s in KEY_BY_LED_SLOT:
                slots.add(KEY_BY_LED_SLOT[s].switch_slot)
        if not slots and self.selected_key_slot is not None:
            if self.selected_key_slot in KEY_BY_SWITCH_SLOT:
                slots.add(self.selected_key_slot)
        return slots

    def set_active_tab(self, tab_name: str) -> None:
        """Switch active configuration tab."""
        self.active_tab = tab_name
        self._notify()

    # -------------------------------------------------------------------------
    # Per-Key RGB Management
    # -------------------------------------------------------------------------

    def get_active_rgb_matrix(self) -> Optional[RGBMatrix]:
        """Get the RGBMatrix instance from the working profile."""
        if not self.working_profile:
            return None
        return self.working_profile.get_rgb_matrix()

    def set_selected_leds_color(self, color: Any) -> None:
        """
        Set color for all currently selected physical LED slots in the working profile.
        Strictly preserves LED_ID byte for all slots.
        """
        if not self.working_profile or not self.selected_led_slots:
            return

        matrix = self.working_profile.get_rgb_matrix()
        for slot in self.selected_led_slots:
            if slot in PHYSICAL_84_LED_SLOTS:
                matrix.set_led(slot, color)

        self.working_profile.set_rgb_matrix(matrix)

        # Coordinate Custom mode: if not already 0x80, switch to 0x80
        if self.working_profile.rgb_global and self.working_profile.rgb_global.effect != EFFECT_CUSTOM:
            self.working_profile.set_custom_per_key_profile(matrix=matrix)

        self.recalculate_diff()
        self._notify()

    def clear_selected_leds(self) -> None:
        """Turn off LED (RGB 0, 0, 0) for all currently selected physical keys."""
        self.set_selected_leds_color((0, 0, 0))

    def fill_all_leds(self, color: Any) -> None:
        """
        Set color for all 84 physical keys in TYPE84_LAYOUT.
        Does not alter reserved slots. Preserves all LED_ID bytes.
        """
        if not self.working_profile:
            return

        matrix = self.working_profile.get_rgb_matrix()
        for k in TYPE84_LAYOUT:
            matrix.set_led(k.led_slot, color)

        self.working_profile.set_rgb_matrix(matrix)

        if self.working_profile.rgb_global and self.working_profile.rgb_global.effect != EFFECT_CUSTOM:
            self.working_profile.set_custom_per_key_profile(matrix=matrix)

        self.recalculate_diff()
        self._notify()

    def clear_all_leds(self) -> None:
        """Turn off all 84 physical keys (set to RGB 0, 0, 0)."""
        self.fill_all_leds((0, 0, 0))

    def sample_color_from_selected(self) -> Optional[Tuple[int, int, int]]:
        """
        Eyedropper: sample the RGB color of the primary selected physical key.
        Returns (R, G, B) tuple or None if no key is selected.
        """
        if self.primary_selected_led is None:
            return None
        matrix = self.get_active_rgb_matrix()
        if not matrix:
            return None
        return matrix.get_led(self.primary_selected_led)

    def activate_custom_per_key_mode(self) -> None:
        """Switch working profile effect to Custom Per-Key (0x80)."""
        self.set_rgb_effect(EFFECT_CUSTOM)

    # -------------------------------------------------------------------------
    # RGB Global Management
    # -------------------------------------------------------------------------

    def get_rgb_effects(self) -> List[Tuple[int, str, RGBEffectMeta]]:
        """
        Get the list of available user RGB effects from RGB_EFFECT_CATALOG.
        Guaranteed:
        - Clean user-facing names without hex IDs (e.g. 'Ripple Spread (Расходящаяся рябь)').
        - Preserves catalog order.
        - Includes Custom Per-Key (0x80).
        - Excludes firmware runtime aliases (0x14, 0x15).
        """
        result: List[Tuple[int, str, RGBEffectMeta]] = []
        for effect_id, meta in RGB_EFFECT_CATALOG.items():
            display_name = f"{meta.name_en} ({meta.name_ru})"
            result.append((effect_id, display_name, meta))
        return result

    def get_current_rgb(self) -> Optional[Dict[str, Any]]:
        """Retrieve current RGB Global values from the working profile."""
        if not self.working_profile or not self.working_profile.rgb_global:
            return None

        rgb = self.working_profile.rgb_global
        r, g, b = rgb.primary
        hex_color = f"#{r:02X}{g:02X}{b:02X}"

        meta = RGB_EFFECT_CATALOG.get(rgb.effect)
        effect_name = (
            f"{meta.name_en} ({meta.name_ru})"
            if meta
            else f"Unknown Effect (0x{rgb.effect:02X})"
        )

        return {
            "effect": rgb.effect,
            "effect_name": effect_name,
            "brightness": rgb.brightness,
            "speed": rgb.speed,
            "color_mode": rgb.color_mode,
            "primary": rgb.primary,
            "primary_hex": hex_color,
            "secondary": rgb.secondary,
            "direction": rgb.direction,
            "meta": meta,
        }

    def set_rgb_effect(self, effect_id: int) -> None:
        """Change RGB effect on working profile using RGBGlobalEditor."""
        if not self.working_profile:
            return

        editor = self.working_profile.get_rgb_editor()
        editor.set_effect(effect_id)
        self.working_profile.set_rgb_global(editor)
        self.recalculate_diff()
        self._notify()

    def set_rgb_brightness(self, brightness: int) -> None:
        """Change RGB brightness (0..5) on working profile."""
        if not self.working_profile:
            return

        editor = self.working_profile.get_rgb_editor()
        editor.set_brightness(brightness)
        self.working_profile.set_rgb_global(editor)
        self.recalculate_diff()
        self._notify()

    def set_rgb_speed(self, speed: int) -> None:
        """Change RGB animation speed (0..5) on working profile."""
        if not self.working_profile:
            return

        editor = self.working_profile.get_rgb_editor()
        editor.set_speed(speed)
        self.working_profile.set_rgb_global(editor)
        self.recalculate_diff()
        self._notify()

    def set_rgb_color_mode(self, color_mode: int) -> None:
        """Change RGB color mode (0=Single Color, 1=Rainbow) on working profile."""
        if not self.working_profile:
            return

        editor = self.working_profile.get_rgb_editor()
        if color_mode == 1:
            editor.set_rainbow()
        else:
            editor.set_single_color()
        self.working_profile.set_rgb_global(editor)
        self.recalculate_diff()
        self._notify()

    def set_rgb_primary_color(self, hex_or_tuple: Any) -> None:
        """Change primary RGB color on working profile."""
        if not self.working_profile:
            return

        editor = self.working_profile.get_rgb_editor()
        editor.set_single_color(hex_or_tuple)
        self.working_profile.set_rgb_global(editor)
        self.recalculate_diff()
        self._notify()

    # -------------------------------------------------------------------------
    # Key Remap (L1) Management
    # -------------------------------------------------------------------------

    def select_key_by_switch(self, switch_slot: Optional[int]) -> None:
        """Select a physical key by switch slot and synchronize LED selection."""
        if switch_slot is None:
            self.selected_key_slot = None
            self._notify()
            return
        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return
        self.selected_key_slot = k_def.switch_slot
        self.select_led(k_def.led_slot, multi=False, toggle=False)

    def get_key_remap_info(
        self,
        switch_slot: Optional[int] = None,
        layer: Optional[int] = None,
    ) -> Optional[KeyRemapInfo]:
        """
        Retrieve structured remap status for a physical switch slot.
        If switch_slot is omitted, queries the currently selected key.
        If layer is omitted, queries the active remap layer (1 or 2).
        """
        if switch_slot is None:
            switch_slot = self.selected_key_slot
        if switch_slot is None:
            return None

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return None

        target_layer = self.active_remap_layer if layer is None else layer
        remap_slot = k_def.remap_slot

        if target_layer == 2:
            is_readonly = (remap_slot == FN_REMAP_SLOT or switch_slot in FN_DISABLED_SWITCH_SLOTS)
            base_rec: Optional[KeyRemapRecord] = None
            if self.device_state and self.device_state.remap_l2:
                base_rec = self.device_state.remap_l2.slots.get(remap_slot)
            curr_rec: Optional[KeyRemapRecord] = None
            if self.working_profile and self.working_profile.remap_l2:
                curr_rec = self.working_profile.remap_l2.slots.get(remap_slot)
        else:
            is_readonly = (remap_slot == FN_REMAP_SLOT)
            base_rec: Optional[KeyRemapRecord] = None
            if self.device_state and self.device_state.remap_l1:
                base_rec = self.device_state.remap_l1.slots.get(remap_slot)
            curr_rec: Optional[KeyRemapRecord] = None
            if self.working_profile and self.working_profile.remap:
                curr_rec = self.working_profile.remap.slots.get(remap_slot)

        # 1. Determine factory default name
        if base_rec and base_rec.scancode != 0:
            default_name = base_rec.hid_name
        elif target_layer == 1 and k_def.key_id in FIRMWARE_FALLBACK_DEFAULTS:
            default_name = f"{FIRMWARE_FALLBACK_DEFAULTS[k_def.key_id][1]} (Firmware Default)"
        elif base_rec and base_rec.is_empty:
            default_name = "[Unbound / Standard]"
        else:
            default_name = k_def.label

        # 2. Determine current status
        if curr_rec is None:
            current_name = default_name
            is_unbound = False
            is_default = True
            is_modified = False
        elif curr_rec.is_empty:
            current_name = "[Disabled / Unbound]"
            is_unbound = True
            is_default = (base_rec is not None and base_rec.is_empty)
            is_modified = (base_rec is not None and not base_rec.is_empty)
        elif curr_rec.scancode == 0 and curr_rec.function_type == 0x02 and target_layer == 1:
            current_name = default_name
            is_unbound = False
            is_default = True
            is_modified = (base_rec is not None and base_rec.to_bytes() != curr_rec.to_bytes())
        else:
            current_name = curr_rec.hid_name
            is_unbound = False
            is_default = (base_rec is not None and curr_rec.to_bytes() == base_rec.to_bytes())
            is_modified = (base_rec is not None and curr_rec.to_bytes() != base_rec.to_bytes())

        return KeyRemapInfo(
            key_id=k_def.key_id,
            label=k_def.label,
            switch_slot=k_def.switch_slot,
            remap_slot=remap_slot,
            default_name=default_name,
            current_name=current_name,
            is_modified=is_modified,
            is_default=is_default,
            is_unbound=is_unbound,
            is_readonly=is_readonly,
            record=curr_rec,
        )

    def set_key_binding(
        self,
        switch_slot: int,
        scancode: int,
        layer: Optional[int] = None,
    ) -> bool:
        """
        Assign a standard HID scancode to the key's remap slot for layer 1 or layer 2.
        Guarantees:
        - Rejects slot outside PHYSICAL_84_REMAP_SLOTS.
        - Rejects modifications to FN_REMAP_SLOT (slot 85).
        - In Layer 2 (Fn): rejects FN_DISABLED_SWITCH_SLOTS (F1..F12).
        - Explicitly resolves function_type via get_function_type_for_scancode.
        - Automatically triggers recalculate_diff() and notifies listeners.
        """
        if not self.working_profile:
            return False

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False

        target_layer = self.active_remap_layer if layer is None else layer
        remap_slot = k_def.remap_slot

        if remap_slot not in PHYSICAL_84_REMAP_SLOTS or remap_slot == FN_REMAP_SLOT:
            return False

        if target_layer == 2 and switch_slot in FN_DISABLED_SWITCH_SLOTS:
            return False

        fn_type = get_function_type_for_scancode(scancode)
        record = KeyRemapRecord.for_standard_key(scancode, page_type=fn_type)

        if target_layer == 2:
            if self.working_profile.remap_l2 is None:
                if self.device_state and self.device_state.remap_l2:
                    from keyboard_re.protocol.keymap import KeymapTable
                    self.working_profile.remap_l2 = KeymapTable(
                        layer=2,
                        slots=dict(self.device_state.remap_l2.slots),
                        raw_bytes=bytes(self.device_state.remap_l2.raw_bytes),
                    )
                else:
                    from keyboard_re.protocol.keymap import KeymapTable
                    self.working_profile.remap_l2 = KeymapTable(layer=2)
            self.working_profile.remap_l2.set_slot(remap_slot, record)
        else:
            if not self.working_profile.remap:
                return False
            self.working_profile.remap.set_slot(remap_slot, record)

        self.recalculate_diff()
        self._notify()
        return True

    def unbind_key(self, switch_slot: int, layer: Optional[int] = None) -> bool:
        """
        Set key remap record to 00 00 00 00 (True Unbound / Disabled).
        Guarantees:
        - Rejects modifications to FN_REMAP_SLOT (slot 85).
        - In Layer 2: rejects FN_DISABLED_SWITCH_SLOTS (F1..F12).
        - Rejects non-physical slots.
        """
        if not self.working_profile:
            return False

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False

        target_layer = self.active_remap_layer if layer is None else layer
        remap_slot = k_def.remap_slot

        if remap_slot not in PHYSICAL_84_REMAP_SLOTS or remap_slot == FN_REMAP_SLOT:
            return False

        if target_layer == 2 and switch_slot in FN_DISABLED_SWITCH_SLOTS:
            return False

        record = KeyRemapRecord(page_type=0, param1=0, param2=0, param3=0)

        if target_layer == 2:
            if self.working_profile.remap_l2 is None:
                if self.device_state and self.device_state.remap_l2:
                    from keyboard_re.protocol.keymap import KeymapTable
                    self.working_profile.remap_l2 = KeymapTable(
                        layer=2,
                        slots=dict(self.device_state.remap_l2.slots),
                        raw_bytes=bytes(self.device_state.remap_l2.raw_bytes),
                    )
                else:
                    from keyboard_re.protocol.keymap import KeymapTable
                    self.working_profile.remap_l2 = KeymapTable(layer=2)
            self.working_profile.remap_l2.set_slot(remap_slot, record)
        else:
            if not self.working_profile.remap:
                return False
            self.working_profile.remap.set_slot(remap_slot, record)

        self.recalculate_diff()
        self._notify()
        return True

    def reset_key_to_default(self, switch_slot: int, layer: Optional[int] = None) -> bool:
        """Restore key remap slot from baseline device_state."""
        if not self.working_profile or not self.device_state:
            return False

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False

        target_layer = self.active_remap_layer if layer is None else layer
        remap_slot = k_def.remap_slot

        if remap_slot not in PHYSICAL_84_REMAP_SLOTS:
            return False

        if target_layer == 2:
            if not self.device_state.remap_l2 or not self.working_profile.remap_l2:
                return False
            orig = self.device_state.remap_l2.slots.get(remap_slot)
            if orig is None:
                return False
            copy_rec = KeyRemapRecord(
                prefix=orig.prefix,
                scancode=orig.scancode,
                special=orig.special,
                function_type=orig.function_type,
            )
            self.working_profile.remap_l2.set_slot(remap_slot, copy_rec)
        else:
            if not self.device_state.remap_l1 or not self.working_profile.remap:
                return False
            orig = self.device_state.remap_l1.slots.get(remap_slot)
            if orig is None:
                return False
            copy_rec = KeyRemapRecord(
                prefix=orig.prefix,
                scancode=orig.scancode,
                special=orig.special,
                function_type=orig.function_type,
            )
            self.working_profile.remap.set_slot(remap_slot, copy_rec)

        self.recalculate_diff()
        self._notify()
        return True

    def reset_all_remap(self, layer: Optional[int] = None) -> bool:
        """Restore all slots in working profile remap from baseline device_state."""
        if not self.working_profile or not self.device_state:
            return False

        target_layer = self.active_remap_layer if layer is None else layer

        if target_layer == 2:
            if not self.device_state.remap_l2:
                return False
            from keyboard_re.protocol.keymap import KeymapTable
            self.working_profile.remap_l2 = KeymapTable(
                layer=2,
                slots={
                    s: KeyRemapRecord(
                        prefix=r.prefix,
                        scancode=r.scancode,
                        special=r.special,
                        function_type=r.function_type,
                    )
                    for s, r in self.device_state.remap_l2.slots.items()
                },
                raw_bytes=bytes(self.device_state.remap_l2.raw_bytes),
            )
        else:
            if not self.device_state.remap_l1 or not self.working_profile.remap:
                return False
            self.working_profile.remap.slots = {
                s: KeyRemapRecord(
                    prefix=r.prefix,
                    scancode=r.scancode,
                    special=r.special,
                    function_type=r.function_type,
                )
                for s, r in self.device_state.remap_l1.slots.items()
            }
            self.working_profile.remap.raw_bytes = bytes(self.device_state.remap_l1.raw_bytes)

        self.recalculate_diff()
        self._notify()
        return True

    def is_key_remapped(self, switch_slot: int, layer: Optional[int] = None) -> bool:
        """Check if a physical key currently has a modified remap record relative to baseline."""
        if not self.working_profile or not self.device_state:
            return False

        target_layer = self.active_remap_layer if layer is None else layer
        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False

        remap_slot = k_def.remap_slot

        if target_layer == 2:
            if not self.working_profile.remap_l2 or not self.device_state.remap_l2:
                return False
            curr = self.working_profile.remap_l2.slots.get(remap_slot)
            base = self.device_state.remap_l2.slots.get(remap_slot)
        else:
            if not self.working_profile.remap or not self.device_state.remap_l1:
                return False
            curr = self.working_profile.remap.slots.get(remap_slot)
            base = self.device_state.remap_l1.slots.get(remap_slot)

        if curr is None or base is None:
            return False
        return curr.to_bytes() != base.to_bytes()

    # -------------------------------------------------------------------------
    # Hall Effect / Rapid Trigger (AA 17 / AA 27) Management
    # -------------------------------------------------------------------------

    def _get_hall_config_by_slot(self, hall_state: Any, switch_slot: int) -> Optional[KeyConfig]:
        """Read 8-byte KeyConfig from a HallProfileState image at the physical switch slot address."""
        if not hall_state or not hasattr(hall_state, "raw_image"):
            return None
        if switch_slot not in PHYSICAL_84_SWITCH_SLOTS:
            return None
        bank = switch_slot // 16
        col = switch_slot % 16
        addr = key_address(bank, col)
        if addr + 8 > len(hall_state.raw_image):
            return None
        return KeyConfig.from_bytes(hall_state.raw_image[addr : addr + 8])

    def get_hall_info(self, switch_slot: Optional[int] = None) -> Optional[KeyHallInfo]:
        """
        Retrieve structured Hall/RT status for a physical switch slot.
        If switch_slot is omitted, queries the currently selected key.
        """
        if switch_slot is None:
            switch_slot = self.selected_key_slot
        if switch_slot is None:
            return None

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return None

        curr_cfg: Optional[KeyConfig] = None
        if self.working_profile and self.working_profile.hall:
            curr_cfg = self._get_hall_config_by_slot(self.working_profile.hall, switch_slot)

        base_cfg: Optional[KeyConfig] = None
        if self.device_state and self.device_state.hall_profile_1:
            base_cfg = self._get_hall_config_by_slot(self.device_state.hall_profile_1, switch_slot)

        act = curr_cfg.actuation_mm if curr_cfg else 1.40
        press = curr_cfg.rt_press_mm if curr_cfg else 0.00
        rel = curr_cfg.rt_release_mm if curr_cfg else 0.00
        flags = curr_cfg.flags if curr_cfg else 0
        rt_enabled = curr_cfg.is_rt_enabled if curr_cfg else False

        def_act = base_cfg.actuation_mm if base_cfg else 1.40
        def_press = base_cfg.rt_press_mm if base_cfg else 0.00
        def_rel = base_cfg.rt_release_mm if base_cfg else 0.00

        is_modified = False
        if curr_cfg and base_cfg:
            is_modified = (
                abs(act - def_act) >= 0.005
                or abs(press - def_press) >= 0.005
                or abs(rel - def_rel) >= 0.005
            )

        return KeyHallInfo(
            key_id=k_def.key_id,
            label=k_def.label,
            switch_slot=k_def.switch_slot,
            actuation_mm=act,
            rt_press_mm=press,
            rt_release_mm=rel,
            is_rt_enabled=rt_enabled,
            flags=flags,
            default_actuation_mm=def_act,
            default_rt_press_mm=def_press,
            default_rt_release_mm=def_rel,
            is_modified=is_modified,
        )

    def get_multi_hall_info(self, switch_slots: Optional[Set[int]] = None) -> MultiKeyHallInfo:
        """
        Inspect all specified physical switches (or currently selected switches)
        and compute common values and mixed-state flags.
        """
        if switch_slots is None:
            switch_slots = self.selected_switch_slots
        valid_slots = sorted(list(switch_slots & PHYSICAL_84_SWITCH_SLOTS))

        if not valid_slots:
            return MultiKeyHallInfo(
                count=0,
                key_labels=[],
                switch_slots=set(),
                actuation_mm=None,
                rt_press_mm=None,
                rt_release_mm=None,
                is_rt_enabled=None,
                has_mixed_actuation=False,
                has_mixed_rt=False,
                is_any_modified=False,
            )

        labels = [KEY_BY_SWITCH_SLOT[s].label for s in valid_slots]
        infos = [self.get_hall_info(s) for s in valid_slots]
        valid_infos = [inf for inf in infos if inf is not None]

        if not valid_infos:
            return MultiKeyHallInfo(
                count=len(valid_slots),
                key_labels=labels,
                switch_slots=set(valid_slots),
                actuation_mm=None,
                rt_press_mm=None,
                rt_release_mm=None,
                is_rt_enabled=None,
                has_mixed_actuation=False,
                has_mixed_rt=False,
                is_any_modified=False,
            )

        # Check actuation consistency
        first_act = valid_infos[0].actuation_mm
        has_mixed_act = any(abs(inf.actuation_mm - first_act) >= 0.005 for inf in valid_infos)
        common_act = None if has_mixed_act else first_act

        # Check RT Press consistency
        first_press = valid_infos[0].rt_press_mm
        has_mixed_press = any(abs(inf.rt_press_mm - first_press) >= 0.005 for inf in valid_infos)
        common_press = None if has_mixed_press else first_press

        # Check RT Release consistency
        first_rel = valid_infos[0].rt_release_mm
        has_mixed_rel = any(abs(inf.rt_release_mm - first_rel) >= 0.005 for inf in valid_infos)
        common_rel = None if has_mixed_rel else first_rel

        # Check RT enabled consistency
        first_rt_en = valid_infos[0].is_rt_enabled
        has_mixed_rt_en = any(inf.is_rt_enabled != first_rt_en for inf in valid_infos)
        common_rt_en = None if has_mixed_rt_en else first_rt_en

        has_mixed_rt = has_mixed_press or has_mixed_rel or has_mixed_rt_en
        is_any_mod = any(inf.is_modified for inf in valid_infos)

        return MultiKeyHallInfo(
            count=len(valid_slots),
            key_labels=labels,
            switch_slots=set(valid_slots),
            actuation_mm=common_act,
            rt_press_mm=common_press,
            rt_release_mm=common_rel,
            is_rt_enabled=common_rt_en,
            has_mixed_actuation=has_mixed_act,
            has_mixed_rt=has_mixed_rt,
            is_any_modified=is_any_mod,
        )

    def set_hall_parameters(
        self,
        switch_slots: Set[int],
        actuation_mm: Optional[float] = None,
        rt_press_mm: Optional[float] = None,
        rt_release_mm: Optional[float] = None,
    ) -> bool:
        """
        Safely update Hall parameters across specified physical switch slots.
        Only updates fields that are provided (not None).
        Leaves flags (+6..+7) completely untouched.
        Guarantees strict boundary check to PHYSICAL_84_SWITCH_SLOTS.
        """
        if not self.working_profile or not self.working_profile.hall:
            return False

        target_slots = switch_slots & PHYSICAL_84_SWITCH_SLOTS
        if not target_slots:
            return False

        for slot in target_slots:
            bank = slot // 16
            col = slot % 16
            addr = key_address(bank, col)
            curr_cfg = KeyConfig.from_bytes(self.working_profile.hall.raw_image[addr : addr + 8])

            if actuation_mm is not None:
                curr_cfg.actuation_mm = max(0.10, min(4.00, round(actuation_mm, 2)))
            if rt_press_mm is not None:
                curr_cfg.rt_press_mm = max(0.00, min(4.00, round(rt_press_mm, 2)))
            if rt_release_mm is not None:
                curr_cfg.rt_release_mm = max(0.00, min(4.00, round(rt_release_mm, 2)))

            if (rt_press_mm is not None and rt_press_mm > 0.0) or (rt_release_mm is not None and rt_release_mm > 0.0):
                curr_cfg.is_rt_enabled = True
            elif rt_press_mm == 0.0 and rt_release_mm == 0.0:
                curr_cfg.is_rt_enabled = False

            self.working_profile.hall.raw_image[addr : addr + 8] = curr_cfg.to_bytes()
            if (bank, col) in REVERSE_KEY_MAP:
                self.working_profile.hall.keys[REVERSE_KEY_MAP[(bank, col)]] = curr_cfg

        self.recalculate_diff()
        self._notify()
        return True

    def set_hall_rt_enabled(
        self,
        switch_slots: Set[int],
        enabled: bool,
        default_press: float = 0.20,
        default_release: float = 0.20,
    ) -> bool:
        """
        Convenience method to enable/disable Rapid Trigger.
        Directly controls flags & 0x01 (vendor isWholeFast).
        - When True: sets flags bit 0 to 1, sets default sensitivities if non-positive.
        - When False: clears flags bit 0.
        """
        if not self.working_profile or not self.working_profile.hall:
            return False

        target_slots = switch_slots & PHYSICAL_84_SWITCH_SLOTS
        if not target_slots:
            return False

        for slot in target_slots:
            bank = slot // 16
            col = slot % 16
            addr = key_address(bank, col)
            curr_cfg = KeyConfig.from_bytes(self.working_profile.hall.raw_image[addr : addr + 8])
            curr_cfg.is_rt_enabled = enabled
            if enabled:
                if curr_cfg.rt_press_mm <= 0.0:
                    curr_cfg.rt_press_mm = default_press
                if curr_cfg.rt_release_mm <= 0.0:
                    curr_cfg.rt_release_mm = default_release
            else:
                curr_cfg.rt_press_mm = 0.00
                curr_cfg.rt_release_mm = 0.00

            self.working_profile.hall.raw_image[addr : addr + 8] = curr_cfg.to_bytes()
            if (bank, col) in REVERSE_KEY_MAP:
                self.working_profile.hall.keys[REVERSE_KEY_MAP[(bank, col)]] = curr_cfg

        self.recalculate_diff()
        self._notify()
        return True

    def reset_hall_keys(self, switch_slots: Set[int]) -> bool:
        """Restore specified physical keys from baseline device_state."""
        if not self.working_profile or not self.working_profile.hall:
            return False
        if not self.device_state or not self.device_state.hall_profile_1:
            return False

        target_slots = switch_slots & PHYSICAL_84_SWITCH_SLOTS
        if not target_slots:
            return False

        for slot in target_slots:
            bank = slot // 16
            col = slot % 16
            addr = key_address(bank, col)
            base_bytes = self.device_state.hall_profile_1.raw_image[addr : addr + 8]
            self.working_profile.hall.raw_image[addr : addr + 8] = base_bytes
            if (bank, col) in REVERSE_KEY_MAP:
                self.working_profile.hall.keys[REVERSE_KEY_MAP[(bank, col)]] = KeyConfig.from_bytes(base_bytes)

        self.recalculate_diff()
        self._notify()
        return True

    def reset_all_hall(self) -> bool:
        """Restore all 84 physical keys from baseline device_state."""
        if not self.working_profile or not self.device_state or not self.device_state.hall_profile_1:
            return False

        self.working_profile.hall = self.device_state.hall_profile_1.clone()
        self.recalculate_diff()
        self._notify()
        return True

    def is_key_hall_modified(self, switch_slot: int) -> bool:
        """Check if a physical key currently has modified Hall parameters relative to baseline."""
        if not self.working_profile or not self.device_state:
            return False
        if not self.working_profile.hall or not self.device_state.hall_profile_1:
            return False
        if switch_slot not in PHYSICAL_84_SWITCH_SLOTS:
            return False

        bank = switch_slot // 16
        col = switch_slot % 16
        addr = key_address(bank, col)
        # Compare all 8 bytes (<BBHHH: axis_type, flags, actuation, rt_press, rt_release)
        curr_bytes = self.working_profile.hall.raw_image[addr : addr + 8]
        base_bytes = self.device_state.hall_profile_1.raw_image[addr : addr + 8]
        return curr_bytes != base_bytes

    # -------------------------------------------------------------------------
    # Dynamic Keystroke (DKS) Management
    # -------------------------------------------------------------------------

    def get_dks_info(self, switch_slot: Optional[int] = None) -> Optional[KeyDKSInfo]:
        """
        Retrieve structured DKS status for a physical switch slot.
        If switch_slot is omitted, queries the currently selected key.
        """
        if switch_slot is None:
            switch_slot = self.selected_key_slot
        if switch_slot is None:
            return None

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def or switch_slot not in PHYSICAL_84_SWITCH_SLOTS:
            return None

        remap_slot = k_def.remap_slot

        # Working profile status
        is_active = False
        dks_slot_idx: Optional[int] = None
        if self.working_profile and self.working_profile.remap:
            rec = self.working_profile.remap.slots.get(remap_slot)
            if rec and rec.is_dks:
                is_active = True
                dks_slot_idx = rec.dks_slot_index

        dks_rec = DKSRecord()
        if is_active and dks_slot_idx is not None and self.working_profile and self.working_profile.dks:
            dks_rec = self.working_profile.dks.get_record(dks_slot_idx)

        # Baseline status
        base_is_active = False
        base_dks_slot_idx: Optional[int] = None
        if self.device_state and self.device_state.remap_l1:
            base_remap = self.device_state.remap_l1.slots.get(remap_slot)
            if base_remap and base_remap.is_dks:
                base_is_active = True
                base_dks_slot_idx = base_remap.dks_slot_index

        base_dks_rec = DKSRecord(
            make_value_1_mm=0.0,
            make_value_2_mm=0.0,
            break_value_1_mm=0.0,
            break_value_2_mm=0.0,
        )
        if base_is_active and base_dks_slot_idx is not None and self.device_state and self.device_state.dks_table:
            base_dks_rec = self.device_state.dks_table.get_record(base_dks_slot_idx)

        is_modified = False
        if is_active != base_is_active or dks_slot_idx != base_dks_slot_idx:
            is_modified = True
        elif is_active:
            is_modified = (dks_rec.to_bytes() != base_dks_rec.to_bytes())

        return KeyDKSInfo(
            key_id=k_def.key_id,
            label=k_def.label,
            switch_slot=switch_slot,
            remap_slot=remap_slot,
            is_active=is_active,
            dks_slot_index=dks_slot_idx,
            make_value_1_mm=dks_rec.make_value_1_mm,
            make_value_2_mm=dks_rec.make_value_2_mm,
            break_value_1_mm=dks_rec.break_value_1_mm,
            break_value_2_mm=dks_rec.break_value_2_mm,
            actions=list(dks_rec.actions),
            states=[list(r) for r in dks_rec.states],
            is_modified=is_modified,
        )

    def is_key_dks(self, switch_slot: int) -> bool:
        """Check if a physical key is currently bound to DKS in working_profile."""
        if not self.working_profile or not self.working_profile.remap:
            return False
        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False
        rec = self.working_profile.remap.slots.get(k_def.remap_slot)
        return bool(rec and rec.is_dks)

    def is_key_dks_modified(self, switch_slot: int) -> bool:
        """Check if a physical key's DKS binding or record differs from baseline."""
        info = self.get_dks_info(switch_slot)
        return bool(info and info.is_modified)

    def set_dks_config(
        self,
        switch_slot: int,
        make_value_1_mm: float = DEFAULT_MAKE_VALUE_1_MM,
        make_value_2_mm: float = DEFAULT_MAKE_VALUE_2_MM,
        break_value_1_mm: float = DEFAULT_BREAK_VALUE_1_MM,
        break_value_2_mm: float = DEFAULT_BREAK_VALUE_2_MM,
        actions: Optional[List[int]] = None,
        states: Optional[List[List[DKSEventState]]] = None,
    ) -> bool:
        """
        Configure or update DKS for a physical key.
        Allocates a DKS slot (0..63) if needed, updates DKSTable, and links remap_l1 via prefix 0x08.
        """
        if not self.working_profile or not self.working_profile.remap:
            return False
        if switch_slot not in PHYSICAL_84_SWITCH_SLOTS:
            return False

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def or k_def.remap_slot == FN_REMAP_SLOT:
            return False

        # Ensure working_profile.dks_raw exists
        if not self.working_profile.dks_raw or len(self.working_profile.dks_raw) != 1024:
            self.working_profile.dks_raw = bytes(1024)

        dks_table = self.working_profile.dks or DKSTable()

        # Check existing slot index
        remap_slot = k_def.remap_slot
        curr_remap = self.working_profile.remap.slots.get(remap_slot)
        dks_slot: Optional[int] = None
        if curr_remap and curr_remap.is_dks:
            dks_slot = curr_remap.dks_slot_index

        if dks_slot is None or dks_slot < 0 or dks_slot >= 64:
            dks_slot = dks_table.allocate_slot()
            if dks_slot is None:
                raise ValueError("Maximum 64 DKS slots reached on device")

        raw_actions = actions if actions is not None else [0, 0, 0, 0]
        raw_states = states if states is not None else [[DKSEventState.OFF] * 4 for _ in range(4)]

        record = DKSRecord(
            make_value_1_mm=max(0.1, min(3.9, round(make_value_1_mm, 2))),
            make_value_2_mm=max(0.2, min(4.0, round(make_value_2_mm, 2))),
            break_value_1_mm=max(0.2, min(4.0, round(break_value_1_mm, 2))),
            break_value_2_mm=max(0.1, min(3.9, round(break_value_2_mm, 2))),
            actions=list(raw_actions[:4]) + [0] * max(0, 4 - len(raw_actions)),
            states=[list(r[:4]) for r in raw_states[:4]],
        )

        dks_table.set_record(dks_slot, record)
        self.working_profile.set_dks_table(dks_table)

        # Update remap Layer 1
        self.working_profile.remap.slots[remap_slot] = KeyRemapRecord.for_dks(dks_slot)
        self.working_profile.remap.raw_bytes = self.working_profile.remap.to_bytes()

        self.recalculate_diff()
        self._notify()
        return True

    def remove_dks(self, switch_slot: int) -> bool:
        """
        Remove DKS configuration from a physical key.
        Frees the DKS slot and restores remap_l1 for this key to baseline.
        """
        if not self.working_profile or not self.working_profile.remap:
            return False
        if switch_slot not in PHYSICAL_84_SWITCH_SLOTS:
            return False

        k_def = KEY_BY_SWITCH_SLOT.get(switch_slot)
        if not k_def:
            return False

        remap_slot = k_def.remap_slot
        curr_remap = self.working_profile.remap.slots.get(remap_slot)
        if not curr_remap or not curr_remap.is_dks:
            return False

        dks_slot = curr_remap.dks_slot_index
        if dks_slot is not None and self.working_profile.dks:
            table = self.working_profile.dks
            table.clear_record(dks_slot)
            self.working_profile.set_dks_table(table)

        # Restore remap_l1 from baseline device_state
        restored = False
        if self.device_state and self.device_state.remap_l1:
            base_rec = self.device_state.remap_l1.slots.get(remap_slot)
            if base_rec and not base_rec.is_dks:
                self.working_profile.remap.slots[remap_slot] = base_rec
                restored = True

        if not restored:
            # Fallback to standard baseline default
            self.working_profile.remap.slots[remap_slot] = KeyRemapRecord(
                prefix=0, scancode=0, special=0, function_type=0x02
            )

        self.working_profile.remap.raw_bytes = self.working_profile.remap.to_bytes()
        self.recalculate_diff()
        self._notify()
        return True

    # -------------------------------------------------------------------------
    # Diff & Confirmation
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Game Mode / System Settings Management (AA 11 / AA 21)
    # -------------------------------------------------------------------------

    def get_game_mode_info(self) -> Optional[GameModeResponse]:
        """Return current GameModeResponse from working profile or baseline."""
        if self.working_profile and self.working_profile.game_mode:
            return self.working_profile.game_mode
        if self.device_state and self.device_state.game_mode:
            return self.device_state.game_mode
        return None

    def _ensure_working_game_mode(self) -> GameModeResponse:
        """Ensure working_profile has a mutable GameModeResponse instance."""
        if not self.working_profile:
            raise RuntimeError("Working profile is not available")
        if self.working_profile.game_mode is None:
            base_gm = self.device_state.game_mode if self.device_state else None
            self.working_profile.game_mode = base_gm.clone() if base_gm else GameModeResponse()
        return self.working_profile.game_mode

    def set_stability_mode(self, enabled: bool) -> None:
        """Enable or disable hardware Stability Mode."""
        gm = self._ensure_working_game_mode()
        gm.stability_mode = 1 if enabled else 0
        self.recalculate_diff()
        self._notify()

    def set_auto_calibration(self, enabled: bool) -> None:
        """Enable or disable hardware Auto Calibration."""
        gm = self._ensure_working_game_mode()
        gm.auto_calibration = 1 if enabled else 0
        self.recalculate_diff()
        self._notify()

    def set_game_mode(self, enabled: bool) -> None:
        """Enable or disable Game Mode (Win key lock)."""
        gm = self._ensure_working_game_mode()
        gm.game_mode = 1 if enabled else 0
        self.recalculate_diff()
        self._notify()

    def set_fn_switch(self, enabled: bool) -> None:
        """Enable or disable Fn Switch inversion."""
        gm = self._ensure_working_game_mode()
        gm.fn_switch = 1 if enabled else 0
        self.recalculate_diff()
        self._notify()

    def set_report_rate_hz(self, hz: int) -> None:
        """Set polling report rate in Hz (1000, 4000, 8000)."""
        gm = self._ensure_working_game_mode()
        gm.set_report_rate_hz(hz)
        self.recalculate_diff()
        self._notify()

    def set_sleep_time(self, minutes: int) -> None:
        """Set sleep timeout in minutes (0..30)."""
        gm = self._ensure_working_game_mode()
        gm.sleep_time = max(0, min(30, int(minutes)))
        self.recalculate_diff()
        self._notify()

    def set_key_delay(self, ms: int) -> None:
        """Set key debounce delay in milliseconds (0..10)."""
        gm = self._ensure_working_game_mode()
        gm.key_delay = max(0, min(10, int(ms)))
        self.recalculate_diff()
        self._notify()

    def set_system_mode(self, mode: int) -> None:
        """Set OS system mode (0 = Windows, 1 = Mac)."""
        gm = self._ensure_working_game_mode()
        gm.system_mode = 1 if mode == 1 else 0
        self.recalculate_diff()
        self._notify()

    def set_deadzones(self, top_mm: float, bottom_mm: float) -> None:
        """Set analog deadzones in mm (0.00 .. 0.50)."""
        gm = self._ensure_working_game_mode()
        gm.top_deadzone = max(0.0, min(1.0, float(top_mm)))
        gm.bottom_deadzone = max(0.0, min(1.0, float(bottom_mm)))
        self.recalculate_diff()
        self._notify()

    def reset_game_mode_to_default(self) -> None:
        """Reset Game Mode settings back to baseline device state."""
        if not self.working_profile or not self.device_state:
            return
        if self.device_state.game_mode:
            self.working_profile.game_mode = self.device_state.game_mode.clone()
        else:
            self.working_profile.game_mode = None
        self.recalculate_diff()
        self._notify()

    # -------------------------------------------------------------------------
    # Macro Management (AA 15 / AA 25)
    # -------------------------------------------------------------------------

    def get_macro_catalog(self) -> MacroCatalog:
        """Return current MacroCatalog from working profile or baseline."""
        if self.working_profile:
            return self.working_profile.macro_catalog
        if self.device_state and self.device_state.macro_table_raw:
            return MacroCatalog.from_full_image(self.device_state.macro_table_raw)
        return MacroCatalog()

    def get_macro_list(self) -> List[MacroDefinition]:
        """Return list of all configured macros sorted by macro_id."""
        catalog = self.get_macro_catalog()
        return [catalog.macros[mid] for mid in sorted(catalog.macros.keys())]

    def get_macro(self, macro_id: int) -> Optional[MacroDefinition]:
        """Return single MacroDefinition by slot ID."""
        return self.get_macro_catalog().get_macro(macro_id)

    def save_macro(self, macro: MacroDefinition) -> None:
        """Save or update a MacroDefinition in working profile."""
        if not self.working_profile:
            raise RuntimeError("Working profile is not available")
        catalog = self.get_macro_catalog()
        catalog.set_macro(macro)
        self.working_profile.set_macro_catalog(catalog)
        self.recalculate_diff()
        self._notify()

    def delete_macro(self, macro_id: int) -> bool:
        """Delete a MacroDefinition by ID."""
        if not self.working_profile:
            return False
        catalog = self.get_macro_catalog()
        if catalog.delete_macro(macro_id):
            self.working_profile.set_macro_catalog(catalog)
            self.recalculate_diff()
            self._notify()
            return True
        return False

    def clear_all_macros(self) -> None:
        """Clear all macros in working profile."""
        if not self.working_profile:
            return
        empty_cat = MacroCatalog()
        self.working_profile.set_macro_catalog(empty_cat)
        self.recalculate_diff()
        self._notify()

    def reset_macros_to_default(self) -> None:
        """Reset macros back to baseline device state."""
        if not self.working_profile or not self.device_state:
            return
        if self.device_state.macro_table_raw:
            self.working_profile.macros_raw = bytes(self.device_state.macro_table_raw)
        else:
            self.working_profile.macros_raw = None
        self.recalculate_diff()
        self._notify()

    def recalculate_diff(self) -> Optional[ProfileDiff]:
        """
        Recalculate diff between baseline DeviceState and working Profile.
        Updates dirty flag and change count.
        """
        if not self.working_profile or not self.device_state:
            self.is_dirty = False
            self.diff_count = 0
            self.last_diff = None
            return None

        diff = self.profile_manager.compare_profile_with_state(
            self.working_profile,
            self.device_state,
        )
        self.last_diff = diff
        self.is_dirty = diff.has_changes
        self.diff_count = diff.total_changes
        return diff

    def get_confirmation_summary(self) -> Optional[PlanConfirmationSummary]:
        """
        Generate dry-run PlanConfirmationSummary without performing any writes.
        """
        if not self.working_profile or not self.device_state:
            return None

        plan = self.profile_manager.create_write_plan(
            self.device_state,
            self.working_profile,
        )
        diff = self.recalculate_diff() or self.profile_manager.compare_profile_with_state(
            self.working_profile,
            self.device_state,
        )
        return build_confirmation_summary(plan, diff)

    # -------------------------------------------------------------------------
    # Apply Pipeline
    # -------------------------------------------------------------------------

    def apply_changes(self, confirmed: bool = True) -> ApplyProfileResult:
        """
        Apply working profile changes through ProfileManager.apply_profile().
        Strict Safety:
        - If not confirmed, aborts with 0 writes.
        - On SUCCESS: updates baseline device_state, resets dirty flag.
        """
        if not self.is_connected or not self.working_profile or not self.device_state:
            return ApplyProfileResult(
                status=ExecutionStatus.ABORTED,
                profile_id=0,
                profile_name="",
                error="Device not connected or working profile missing",
            )

        if not confirmed:
            return ApplyProfileResult(
                status=ExecutionStatus.ABORTED,
                profile_id=self.working_profile.profile_id,
                profile_name=self.working_profile.name,
                error="Confirmation rejected by user",
            )

        # In mock mode, supply simulated readback function
        readback_func = None
        applied_state: Optional[DeviceState] = None
        if self.is_mock:
            applied_state = self.device_state.clone()
            if self.working_profile.rgb_global:
                applied_state.rgb_global = copy.deepcopy(self.working_profile.rgb_global)
            if self.working_profile.rgb_matrix:
                applied_state.rgb_matrix_raw = bytearray(self.working_profile.rgb_matrix)
            if self.working_profile.remap:
                applied_state.remap_l1 = copy.deepcopy(self.working_profile.remap)
            if self.working_profile.remap_l2:
                applied_state.remap_l2 = copy.deepcopy(self.working_profile.remap_l2)
            if self.working_profile.hall:
                applied_state.hall_profile_1 = self.working_profile.hall.clone()
            if self.working_profile.dks_raw:
                applied_state.dks_table_raw = bytes(self.working_profile.dks_raw)
            if self.working_profile.game_mode:
                applied_state.game_mode = self.working_profile.game_mode.clone()
            if self.working_profile.macros_raw:
                applied_state.macro_table_raw = bytes(self.working_profile.macros_raw)
            readback_func = lambda tr, timeout: applied_state

        result: ApplyProfileResult = self.profile_manager.apply_profile(
            transport=self.transport,
            profile=self.working_profile,
            current_state=self.device_state,
            confirm_callback=lambda summary: True,  # Already confirmed via UI dialog
            readback_func=readback_func,
        )

        self.last_apply_result = result

        if result.is_success:
            if self.is_mock and applied_state is not None:
                self.device_state = applied_state
            elif result.execution_result and result.execution_result.readback_state:
                self.device_state = result.execution_result.readback_state

            self.recalculate_diff()
            self._notify()

        return result
