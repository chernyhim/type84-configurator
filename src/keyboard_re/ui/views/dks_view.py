"""
Dynamic Keystroke (DKS) View for IO by Red Square Type 84 Configurator.

Provides interactive controls for:
- Inspecting DKS binding status for the selected physical key.
- Configuring 4-stage analog travel thresholds (make1, make2, break1, break2).
- Configuring up to 4 actions with a 4x4 matrix of event states:
  * OFF (inactive)
  * TAP (single trigger on travel event - low nibble bitmask)
  * HOLD (continuous hold across stages - high nibble bitmask)
- Two-way synchronization with Remap Layer 1 (prefix 0x08).
- Safe application to working profile and closed-loop verification.

===============================================================================
EVIDENCE CLASSIFICATION & PROTOCOL STATUS:
===============================================================================
1. TIER 1: PHYSICALLY CONFIRMED:
   - AA 18 read (1024B) / AA 28 write (1024B, 19 chunks).
   - 64 DKS record slots of 16 bytes.
   - Indirection via Remap Layer 1 (prefix 0x08, scancode = dks_slot_index).
   - Travel threshold makeValue1 (+0, 0.1 mm increments; verified via semantic ABA write/readback).
   - Travel thresholds makeValue2, breakValue1, breakValue2 (+1..+3 in 0.1 mm).
   - Scancode offsets at +5 (Action 1) and +7 (Action 2).
   - State bytes +12..+15 for Points 0, 1, 2, 3 (all physically confirmed on hardware).
   - Action bits: Act 1 (0x01/0x10), Act 2 (0x02/0x20), Act 3 (0x04/0x40), Act 4 (0x08/0x80) on all points.

2. TIER 2: STRUCTURAL INFERENCE:
   - Action 3 (+9) and Action 4 (+11) scancode slots.

3. TIER 3: VENDOR JS EVIDENCE / UNVERIFIED PHYSICAL SEMANTICS:
   - Theoretical collision precedence (0x11): vendor JS checks TAP first, Python checks HOLD first.
   - Firmware behavior under collision unverified (never produced by vendor UI).

4. TIER 4: MODEL ASSUMPTIONS:
   - UI 3-state cycling: OFF -> TAP -> HOLD -> OFF via (curr + 1) % 3.
   - Default travel thresholds: 1.6 / 3.0 / 3.0 / 1.6 mm.
===============================================================================
"""

from __future__ import annotations

from typing import Dict, List, Optional
import customtkinter as ctk

from keyboard_re.models.dks import (
    DEFAULT_BREAK_VALUE_1_MM,
    DEFAULT_BREAK_VALUE_2_MM,
    DEFAULT_MAKE_VALUE_1_MM,
    DEFAULT_MAKE_VALUE_2_MM,
    DKSEventState,
)
from keyboard_re.protocol.keymap import HID_USAGE_NAMES
from keyboard_re.ui.controller import AppController, KeyDKSInfo
from keyboard_re.ui.layout_data import KEY_BY_SWITCH_SLOT, PHYSICAL_84_SWITCH_SLOTS

# Popular keys for quick selection dropdown
COMMON_DKS_KEYS: List[tuple[str, int]] = [
    ("None", 0x00),
    ("W", 0x1A), ("A", 0x04), ("S", 0x16), ("D", 0x07),
    ("Q", 0x14), ("E", 0x08), ("R", 0x15), ("F", 0x09),
    ("Space", 0x2C), ("L-Shift", 0xE1), ("L-Ctrl", 0xE0), ("L-Alt", 0xE2),
    ("1!", 0x1E), ("2@", 0x1F), ("3#", 0x20), ("4$", 0x21),
    ("Tab", 0x2B), ("CapsLock", 0x39), ("Enter", 0x28), ("Escape", 0x29),
    ("UpArrow", 0x52), ("DownArrow", 0x51), ("LeftArrow", 0x50), ("RightArrow", 0x4F),
]

# Build comprehensive list of HID keys
ALL_HID_KEY_ITEMS: List[tuple[str, int]] = [("None", 0x00)] + sorted(
    [(name, sc) for sc, name in HID_USAGE_NAMES.items() if sc not in (0x00, 0xAF)],
    key=lambda x: (x[1] >= 0xE0, x[0]),
)
SCANCODE_TO_LABEL: Dict[int, str] = {sc: name for name, sc in ALL_HID_KEY_ITEMS}
LABEL_TO_SCANCODE: Dict[str, int] = {name: sc for name, sc in ALL_HID_KEY_ITEMS}


class DKSView(ctk.CTkFrame):
    """Configuration pane for Dynamic Keystroke (DKS)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.controller = controller

        # Local buffer for editing currently selected key's DKS parameters
        self._current_switch_slot: Optional[int] = None
        self._is_active: bool = False
        self._dks_slot_index: Optional[int] = None
        self._make1: float = DEFAULT_MAKE_VALUE_1_MM
        self._make2: float = DEFAULT_MAKE_VALUE_2_MM
        self._break1: float = DEFAULT_BREAK_VALUE_1_MM
        self._break2: float = DEFAULT_BREAK_VALUE_2_MM
        self._actions: List[int] = [0, 0, 0, 0]
        # 4 actions x 4 travel points
        self._states: List[List[DKSEventState]] = [
            [DKSEventState.OFF] * 4 for _ in range(4)
        ]

        self._is_updating_ui: bool = False

        self.grid_columnconfigure(0, weight=1, uniform="dks_cols")
        self.grid_columnconfigure(1, weight=1, uniform="dks_cols")

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # =====================================================================
        # Left Column: Key Inspector & Travel Points
        # =====================================================================
        left_col = ctk.CTkFrame(self, fg_color=("#f4f4f5", "#18181b"), corner_radius=8)
        left_col.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="nsew")
        left_col.grid_columnconfigure(0, weight=1)

        # Header Title & Status
        self.header_title = ctk.CTkLabel(
            left_col,
            text="Dynamic Keystroke (DKS)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.header_title.grid(row=0, column=0, padx=15, pady=(15, 2), sticky="w")

        self.header_detail = ctk.CTkLabel(
            left_col,
            text="Select a physical key on the keyboard canvas above to edit DKS",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.header_detail.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        # Key Status & Toggle Frame
        status_frame = ctk.CTkFrame(left_col, fg_color="#27272a", corner_radius=6)
        status_frame.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="ew")
        status_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            status_frame,
            text="Status: No key selected",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f4f4f5",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, padx=12, pady=10, sticky="w")

        self.toggle_btn = ctk.CTkButton(
            status_frame,
            text="Enable DKS on Key",
            width=140,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#0891b2",
            hover_color="#0e7490",
            command=self._on_toggle_dks,
        )
        self.toggle_btn.grid(row=0, column=1, padx=12, pady=10, sticky="e")

        # Separator
        sep1 = ctk.CTkFrame(left_col, height=1, fg_color="#27272a")
        sep1.grid(row=3, column=0, padx=15, pady=(0, 15), sticky="ew")

        # Travel Points Header & Reset
        tp_header = ctk.CTkFrame(left_col, fg_color="transparent")
        tp_header.grid(row=4, column=0, padx=15, pady=(0, 10), sticky="ew")
        tp_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tp_header,
            text="Analog Travel Thresholds (0.1 mm step)",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self.reset_trip_btn = ctk.CTkButton(
            tp_header,
            text="Reset (1.6 / 3.0 mm)",
            width=120,
            height=24,
            font=ctk.CTkFont(size=10),
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self._on_reset_travel_points,
        )
        self.reset_trip_btn.grid(row=0, column=1, sticky="e")

        # 4 Sliders for Travel Points
        # 1. Downstroke 1 (make1)
        self.make1_label = ctk.CTkLabel(
            left_col,
            text=f"Downstroke 1 (Press Start): {self._make1:.1f} mm",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.make1_label.grid(row=5, column=0, padx=15, pady=(4, 0), sticky="w")
        self.make1_slider = ctk.CTkSlider(
            left_col,
            from_=0.1,
            to=3.9,
            number_of_steps=38,
            command=self._on_make1_change,
        )
        self.make1_slider.grid(row=6, column=0, padx=15, pady=(2, 8), sticky="ew")

        # 2. Downstroke 2 (make2)
        self.make2_label = ctk.CTkLabel(
            left_col,
            text=f"Downstroke 2 (Bottom Out): {self._make2:.1f} mm",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.make2_label.grid(row=7, column=0, padx=15, pady=(4, 0), sticky="w")
        self.make2_slider = ctk.CTkSlider(
            left_col,
            from_=0.2,
            to=4.0,
            number_of_steps=38,
            command=self._on_make2_change,
        )
        self.make2_slider.grid(row=8, column=0, padx=15, pady=(2, 8), sticky="ew")

        # 3. Upstroke 1 (break1)
        self.break1_label = ctk.CTkLabel(
            left_col,
            text=f"Upstroke 1 (Release Start): {self._break1:.1f} mm",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.break1_label.grid(row=9, column=0, padx=15, pady=(4, 0), sticky="w")
        self.break1_slider = ctk.CTkSlider(
            left_col,
            from_=0.2,
            to=4.0,
            number_of_steps=38,
            command=self._on_break1_change,
        )
        self.break1_slider.grid(row=10, column=0, padx=15, pady=(2, 8), sticky="ew")

        # 4. Upstroke 2 (break2)
        self.break2_label = ctk.CTkLabel(
            left_col,
            text=f"Upstroke 2 (Full Release): {self._break2:.1f} mm",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.break2_label.grid(row=11, column=0, padx=15, pady=(4, 0), sticky="w")
        self.break2_slider = ctk.CTkSlider(
            left_col,
            from_=0.1,
            to=3.9,
            number_of_steps=38,
            command=self._on_break2_change,
        )
        self.break2_slider.grid(row=12, column=0, padx=15, pady=(2, 12), sticky="ew")

        # =====================================================================
        # Right Column: 4 Actions x 4 Travel Points Matrix
        # =====================================================================
        right_col = ctk.CTkFrame(self, fg_color=("#f4f4f5", "#18181b"), corner_radius=8)
        right_col.grid(row=0, column=1, padx=(5, 10), pady=10, sticky="nsew")
        right_col.grid_columnconfigure(0, weight=1)

        # Header
        ctk.CTkLabel(
            right_col,
            text="Action Binding Matrix",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=15, pady=(15, 2), sticky="w")

        ctk.CTkLabel(
            right_col,
            text="Click cell to cycle: OFF -> TAP -> HOLD -> OFF",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
            anchor="w",
        ).grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        # Grid Card Frame
        matrix_card = ctk.CTkFrame(right_col, fg_color="#202023", corner_radius=6)
        matrix_card.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="ew")
        matrix_card.grid_columnconfigure(0, weight=2)
        for c in range(1, 5):
            matrix_card.grid_columnconfigure(c, weight=1)

        # Column Headers
        col_titles = ["Action Key", "Down 1\n(make1)", "Down 2\n(make2)", "Up 1\n(break1)", "Up 2\n(break2)"]
        for c, title in enumerate(col_titles):
            lbl = ctk.CTkLabel(
                matrix_card,
                text=title,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#a1a1aa",
                anchor="center",
            )
            lbl.grid(row=0, column=c, padx=4, pady=6, sticky="ew")

        # 4 Action Rows
        self.action_combos: List[ctk.CTkComboBox] = []
        self.state_buttons: List[List[ctk.CTkButton]] = []

        combo_values = [item[0] for item in ALL_HID_KEY_ITEMS]

        for a_idx in range(4):
            row_num = a_idx + 1
            # Action Dropdown
            combo = ctk.CTkComboBox(
                matrix_card,
                values=combo_values,
                width=100,
                height=26,
                font=ctk.CTkFont(size=11),
                command=lambda val, idx=a_idx: self._on_action_select(idx, val),
            )
            combo.grid(row=row_num, column=0, padx=6, pady=4, sticky="ew")
            self.action_combos.append(combo)

            # 4 Travel Point State Buttons
            row_buttons: List[ctk.CTkButton] = []
            for p_idx in range(4):
                btn = ctk.CTkButton(
                    matrix_card,
                    text="OFF",
                    width=42,
                    height=26,
                    font=ctk.CTkFont(size=10, weight="bold"),
                    fg_color="#27272a",
                    hover_color="#3f3f46",
                    command=lambda a=a_idx, p=p_idx: self._on_cell_click(a, p),
                )
                btn.grid(row=row_num, column=p_idx + 1, padx=3, pady=4)
                row_buttons.append(btn)
            self.state_buttons.append(row_buttons)

        # Button Controls at Bottom
        btn_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        btn_frame.grid(row=3, column=0, padx=15, pady=(5, 15), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.apply_btn = ctk.CTkButton(
            btn_frame,
            text="Apply DKS to Key",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#0891b2",
            hover_color="#0e7490",
            height=32,
            command=self._on_apply,
        )
        self.apply_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.remove_btn = ctk.CTkButton(
            btn_frame,
            text="Remove DKS",
            font=ctk.CTkFont(size=12),
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            height=32,
            command=self._on_remove,
        )
        self.remove_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

    # =========================================================================
    # UI Updates & Event Handlers
    # =========================================================================

    def update_display(self) -> None:
        """Update DKS controls reflecting currently selected key."""
        if self._is_updating_ui:
            return

        self._is_updating_ui = True
        try:
            slot = self.controller.selected_key_slot
            if slot is None or slot not in PHYSICAL_84_SWITCH_SLOTS:
                self._current_switch_slot = None
                self.header_detail.configure(text="No key selected. Click a physical key above to configure DKS.")
                self.status_label.configure(text="Status: No key selected", text_color="#a1a1aa")
                self.toggle_btn.configure(state="disabled", text="Enable DKS on Key")
                self._set_controls_state(False)
                return

            self._current_switch_slot = slot
            k_def = KEY_BY_SWITCH_SLOT[slot]
            d_info = self.controller.get_dks_info(slot)

            if not d_info or not d_info.is_active:
                self._is_active = False
                self._dks_slot_index = None
                self.header_detail.configure(text=f"Selected Key: '{k_def.label}' (Switch #{slot})  |  DKS is inactive")
                self.status_label.configure(text="Status: DKS Inactive", text_color="#a1a1aa")
                self.toggle_btn.configure(state="normal", text="Enable DKS on Key", fg_color="#0891b2")
                self._set_controls_state(False)
                return

            # DKS is active
            self._is_active = True
            self._dks_slot_index = d_info.dks_slot_index
            self._make1 = d_info.make_value_1_mm
            self._make2 = d_info.make_value_2_mm
            self._break1 = d_info.break_value_1_mm
            self._break2 = d_info.break_value_2_mm
            self._actions = list(d_info.actions)
            self._states = [list(r) for r in d_info.states]

            mod_text = " [Modified]" if d_info.is_modified else ""
            self.header_detail.configure(
                text=f"Selected Key: '{k_def.label}' (Switch #{slot})  |  DKS Slot #{self._dks_slot_index}{mod_text}"
            )
            self.status_label.configure(
                text=f"Status: Active (DKS Slot #{self._dks_slot_index})",
                text_color="#06b6d4",
            )
            self.toggle_btn.configure(state="normal", text="Disable DKS", fg_color="#3f3f46")
            self._set_controls_state(True)

            # Update sliders
            self.make1_slider.set(self._make1)
            self.make1_label.configure(text=f"Downstroke 1 (Press Start): {self._make1:.1f} mm")
            self.make2_slider.set(self._make2)
            self.make2_label.configure(text=f"Downstroke 2 (Bottom Out): {self._make2:.1f} mm")
            self.break1_slider.set(self._break1)
            self.break1_label.configure(text=f"Upstroke 1 (Release Start): {self._break1:.1f} mm")
            self.break2_slider.set(self._break2)
            self.break2_label.configure(text=f"Upstroke 2 (Full Release): {self._break2:.1f} mm")

            # Update action combos and state buttons
            for a_idx in range(4):
                sc = self._actions[a_idx]
                label_text = SCANCODE_TO_LABEL.get(sc, f"Key_0x{sc:02X}" if sc else "None")
                self.action_combos[a_idx].set(label_text)

                for p_idx in range(4):
                    st = self._states[a_idx][p_idx]
                    self._update_button_visual(self.state_buttons[a_idx][p_idx], st)

        finally:
            self._is_updating_ui = False

    def _set_controls_state(self, enabled: bool) -> None:
        state_str = "normal" if enabled else "disabled"
        self.reset_trip_btn.configure(state=state_str)
        self.make1_slider.configure(state=state_str)
        self.make2_slider.configure(state=state_str)
        self.break1_slider.configure(state=state_str)
        self.break2_slider.configure(state=state_str)
        self.apply_btn.configure(state=state_str)
        self.remove_btn.configure(state=state_str)
        for combo in self.action_combos:
            combo.configure(state=state_str)
        for row in self.state_buttons:
            for btn in row:
                btn.configure(state=state_str)

    def _update_button_visual(self, btn: ctk.CTkButton, state: DKSEventState) -> None:
        if state == DKSEventState.TAP:
            btn.configure(text="TAP", fg_color="#0891b2", hover_color="#0e7490", text_color="#ffffff")
        elif state == DKSEventState.HOLD:
            btn.configure(text="HOLD", fg_color="#9333ea", hover_color="#7e22ce", text_color="#ffffff")
        else:
            btn.configure(text="OFF", fg_color="#27272a", hover_color="#3f3f46", text_color="#a1a1aa")

    def _on_toggle_dks(self) -> None:
        slot = self._current_switch_slot
        if slot is None:
            return
        if not self._is_active:
            # Enable DKS with defaults
            self._is_active = True
            self._make1 = DEFAULT_MAKE_VALUE_1_MM
            self._make2 = DEFAULT_MAKE_VALUE_2_MM
            self._break1 = DEFAULT_BREAK_VALUE_1_MM
            self._break2 = DEFAULT_BREAK_VALUE_2_MM
            # Default action 1: current key's physical scancode if available
            k_def = KEY_BY_SWITCH_SLOT.get(slot)
            default_sc = 0x04  # 'A' fallback
            if k_def and self.controller.working_profile and self.controller.working_profile.remap:
                base_rec = self.controller.working_profile.remap.slots.get(k_def.remap_slot)
                if base_rec and base_rec.scancode != 0:
                    default_sc = base_rec.scancode
            self._actions = [default_sc, 0, 0, 0]
            # Standard DKS curve: Action 1 TAP on Down1, HOLD on Down2
            self._states = [
                [DKSEventState.TAP, DKSEventState.HOLD, DKSEventState.HOLD, DKSEventState.OFF],
                [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],
                [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],
                [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],
            ]
            self.controller.set_dks_config(
                slot, self._make1, self._make2, self._break1, self._break2, self._actions, self._states
            )
        else:
            self.controller.remove_dks(slot)

        self.update_display()

    def _on_make1_change(self, val: float) -> None:
        self._make1 = round(val, 1)
        if self._make2 <= self._make1:
            self._make2 = min(4.0, round(self._make1 + 0.1, 1))
            self.make2_slider.set(self._make2)
            self.make2_label.configure(text=f"Downstroke 2 (Bottom Out): {self._make2:.1f} mm")
        self.make1_label.configure(text=f"Downstroke 1 (Press Start): {self._make1:.1f} mm")

    def _on_make2_change(self, val: float) -> None:
        self._make2 = round(val, 1)
        if self._make1 >= self._make2:
            self._make1 = max(0.1, round(self._make2 - 0.1, 1))
            self.make1_slider.set(self._make1)
            self.make1_label.configure(text=f"Downstroke 1 (Press Start): {self._make1:.1f} mm")
        self.make2_label.configure(text=f"Downstroke 2 (Bottom Out): {self._make2:.1f} mm")

    def _on_break1_change(self, val: float) -> None:
        self._break1 = round(val, 1)
        if self._break2 >= self._break1:
            self._break2 = max(0.1, round(self._break1 - 0.1, 1))
            self.break2_slider.set(self._break2)
            self.break2_label.configure(text=f"Upstroke 2 (Full Release): {self._break2:.1f} mm")
        self.break1_label.configure(text=f"Upstroke 1 (Release Start): {self._break1:.1f} mm")

    def _on_break2_change(self, val: float) -> None:
        self._break2 = round(val, 1)
        if self._break1 <= self._break2:
            self._break1 = min(4.0, round(self._break2 + 0.1, 1))
            self.break1_slider.set(self._break1)
            self.break1_label.configure(text=f"Upstroke 1 (Release Start): {self._break1:.1f} mm")
        self.break2_label.configure(text=f"Upstroke 2 (Full Release): {self._break2:.1f} mm")

    def _on_reset_travel_points(self) -> None:
        self._make1 = DEFAULT_MAKE_VALUE_1_MM
        self._make2 = DEFAULT_MAKE_VALUE_2_MM
        self._break1 = DEFAULT_BREAK_VALUE_1_MM
        self._break2 = DEFAULT_BREAK_VALUE_2_MM
        self.make1_slider.set(self._make1)
        self.make2_slider.set(self._make2)
        self.break1_slider.set(self._break1)
        self.break2_slider.set(self._break2)
        self.make1_label.configure(text=f"Downstroke 1 (Press Start): {self._make1:.1f} mm")
        self.make2_label.configure(text=f"Downstroke 2 (Bottom Out): {self._make2:.1f} mm")
        self.break1_label.configure(text=f"Upstroke 1 (Release Start): {self._break1:.1f} mm")
        self.break2_label.configure(text=f"Upstroke 2 (Full Release): {self._break2:.1f} mm")

    def _on_action_select(self, action_idx: int, label_text: str) -> None:
        sc = LABEL_TO_SCANCODE.get(label_text, 0)
        self._actions[action_idx] = sc

    def _on_cell_click(self, action_idx: int, point_idx: int) -> None:
        curr = self._states[action_idx][point_idx]
        next_state = DKSEventState((int(curr) + 1) % 3)
        self._states[action_idx][point_idx] = next_state
        self._update_button_visual(self.state_buttons[action_idx][point_idx], next_state)

    def _on_apply(self) -> None:
        slot = self._current_switch_slot
        if slot is None:
            return
        self.controller.set_dks_config(
            slot,
            make_value_1_mm=self._make1,
            make_value_2_mm=self._make2,
            break_value_1_mm=self._break1,
            break_value_2_mm=self._break2,
            actions=self._actions,
            states=self._states,
        )
        self.update_display()

    def _on_remove(self) -> None:
        slot = self._current_switch_slot
        if slot is None:
            return
        self.controller.remove_dks(slot)
        self.update_display()
