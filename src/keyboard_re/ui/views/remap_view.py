"""
Key Remap Layer 1 (Base Layer) View for IO by Red Square Type 84 Configurator.

Provides interactive controls for:
- Inspecting physical key binding, factory default, and dirty state.
- Compact categorized catalog of standard HID scancodes with search.
- Assigning new bindings to the selected physical key.
- Disabling / unbinding physical keys (00 00 00 00).
- Resetting individual keys or all remap changes back to baseline.
- Strict read-only protection for Fn key (slot 86).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import customtkinter as ctk

from keyboard_re.ui.controller import AppController, KeyRemapInfo
from keyboard_re.ui.layout_data import (
    FN_DISABLED_SWITCH_SLOTS,
    FN_REMAP_SLOT,
    KEY_BY_SWITCH_SLOT,
    PHYSICAL_84_REMAP_SLOTS,
)

CATEGORY_COLUMNS: Dict[str, int] = {
    "Letters": 8,
    "Numbers": 7,
    "Modifiers": 4,
    "Navigation": 5,
    "Function": 6,
    "Keypad": 6,
}

REMAP_CATALOG: Dict[str, List[Tuple[int, str]]] = {
    "Letters": [
        (0x04, "A"), (0x05, "B"), (0x06, "C"), (0x07, "D"), (0x08, "E"),
        (0x09, "F"), (0x0A, "G"), (0x0B, "H"), (0x0C, "I"), (0x0D, "J"),
        (0x0E, "K"), (0x0F, "L"), (0x10, "M"), (0x11, "N"), (0x12, "O"),
        (0x13, "P"), (0x14, "Q"), (0x15, "R"), (0x16, "S"), (0x17, "T"),
        (0x18, "U"), (0x19, "V"), (0x1A, "W"), (0x1B, "X"), (0x1C, "Y"),
        (0x1D, "Z"),
    ],
    "Numbers": [
        (0x1E, "1 !"), (0x1F, "2 @"), (0x20, "3 #"), (0x21, "4 $"), (0x22, "5 %"),
        (0x23, "6 ^"), (0x24, "7 &"), (0x25, "8 *"), (0x26, "9 ("), (0x27, "0 )"),
        (0x2D, "- _"), (0x2E, "= +"), (0x2F, "[ {"), (0x30, "] }"), (0x31, "\\ |"),
        (0x33, "; :"), (0x34, "' \""), (0x35, "` ~"), (0x36, ", <"), (0x37, ". >"),
        (0x38, "/ ?"),
    ],
    "Modifiers": [
        (0xE0, "L-Ctrl"), (0xE1, "L-Shift"), (0xE2, "L-Alt"), (0xE3, "L-Win"),
        (0xE4, "R-Ctrl"), (0xE5, "R-Shift"), (0xE6, "R-Alt"), (0xE7, "R-Win"),
        (0x39, "CapsLock"),
    ],
    "Navigation": [
        (0x28, "Enter"), (0x29, "Escape"), (0x2A, "Backspace"), (0x2B, "Tab"),
        (0x2C, "Space"), (0x49, "Insert"), (0x4C, "Delete"), (0x4A, "Home"),
        (0x4D, "End"), (0x4B, "PageUp"), (0x4E, "PageDown"), (0x52, "↑ (Up)"),
        (0x51, "↓ (Down)"), (0x50, "← (Left)"), (0x4F, "→ (Right)"),
    ],
    "Function": [
        (0x3A, "F1"), (0x3B, "F2"), (0x3C, "F3"), (0x3D, "F4"), (0x3E, "F5"),
        (0x3F, "F6"), (0x40, "F7"), (0x41, "F8"), (0x42, "F9"), (0x43, "F10"),
        (0x44, "F11"), (0x45, "F12"), (0x46, "PrintScreen"), (0x47, "ScrollLock"),
        (0x48, "Pause"),
    ],
    "Keypad": [
        (0x53, "NumLock"), (0x54, "KP /"), (0x55, "KP *"), (0x56, "KP -"),
        (0x57, "KP +"), (0x58, "KP Enter"), (0x59, "KP 1"), (0x5A, "KP 2"),
        (0x5B, "KP 3"), (0x5C, "KP 4"), (0x5D, "KP 5"), (0x5E, "KP 6"),
        (0x5F, "KP 7"), (0x60, "KP 8"), (0x61, "KP 9"), (0x62, "KP 0"),
        (0x63, "KP ."),
    ],
}

FULL_KEY_DESCRIPTIONS: Dict[int, str] = {
    0x04: "Letter A", 0x05: "Letter B", 0x06: "Letter C", 0x07: "Letter D",
    0x08: "Letter E", 0x09: "Letter F", 0x0A: "Letter G", 0x0B: "Letter H",
    0x0C: "Letter I", 0x0D: "Letter J", 0x0E: "Letter K", 0x0F: "Letter L",
    0x10: "Letter M", 0x11: "Letter N", 0x12: "Letter O", 0x13: "Letter P",
    0x14: "Letter Q", 0x15: "Letter R", 0x16: "Letter S", 0x17: "Letter T",
    0x18: "Letter U", 0x19: "Letter V", 0x1A: "Letter W", 0x1B: "Letter X",
    0x1C: "Letter Y", 0x1D: "Letter Z",
    0x1E: "Digit 1 / Exclamation (!)",
    0x1F: "Digit 2 / At (@)",
    0x20: "Digit 3 / Hash (#)",
    0x21: "Digit 4 / Dollar ($)",
    0x22: "Digit 5 / Percent (%)",
    0x23: "Digit 6 / Caret (^)",
    0x24: "Digit 7 / Ampersand (&)",
    0x25: "Digit 8 / Asterisk (*)",
    0x26: "Digit 9 / Parenthesis Open (()",
    0x27: "Digit 0 / Parenthesis Close ())",
    0x28: "Enter / Return",
    0x29: "Escape",
    0x2A: "Backspace",
    0x2B: "Tab",
    0x2C: "Spacebar",
    0x2D: "Minus / Underscore (- _)",
    0x2E: "Equal / Plus (= +)",
    0x2F: "Bracket Left ([ {)",
    0x30: "Bracket Right (] })",
    0x31: "Backslash / Pipe (\\ |)",
    0x33: "Semicolon / Colon (; :)",
    0x34: "Quote / Double Quote (' \")",
    0x35: "Grave Accent / Tilde (` ~)",
    0x36: "Comma / Less Than (, <)",
    0x37: "Period / Greater Than (. >)",
    0x38: "Slash / Question Mark (/ ?)",
    0x39: "Caps Lock",
    0x46: "Print Screen",
    0x47: "Scroll Lock",
    0x48: "Pause / Break",
    0x49: "Insert",
    0x4A: "Home",
    0x4B: "Page Up",
    0x4C: "Delete",
    0x4D: "End",
    0x4E: "Page Down (Extended Navigation)",
    0x4F: "Right Arrow (→)",
    0x50: "Left Arrow (←)",
    0x51: "Down Arrow (↓)",
    0x52: "Up Arrow (↑)",
    0x53: "Keypad NumLock",
    0x54: "Keypad Divide (/)",
    0x55: "Keypad Multiply (*)",
    0x56: "Keypad Subtract (-)",
    0x57: "Keypad Add (+)",
    0x58: "Keypad Enter",
    0x63: "Keypad Decimal (.)",
    0xE0: "Left Control (L-Ctrl)",
    0xE1: "Left Shift (L-Shift)",
    0xE2: "Left Alt (L-Alt)",
    0xE3: "Left Windows / GUI (L-Win)",
    0xE4: "Right Control (R-Ctrl)",
    0xE5: "Right Shift (R-Shift)",
    0xE6: "Right Alt (R-Alt)",
    0xE7: "Right Windows / GUI (R-Win)",
}


class KeyRemapView(ctk.CTkFrame):
    """View panel for Key Remapping Layer 1 (Base Layer)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller

        self.current_category: str = "Letters"
        self.selected_target_scancode: Optional[int] = None
        self.selected_target_name: Optional[str] = None

        self.grid_columnconfigure(0, weight=0, minsize=320)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_widgets()
        self.controller.subscribe(self.update_display)
        self.update_display()

    def _build_widgets(self) -> None:
        # 1. Header Frame
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, padx=15, pady=(10, 4), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            header,
            text="Key Remapping (Base Layer 1)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        # Layer switch: Base Layer (L1) / Fn Layer (L2)
        self.layer_selector = ctk.CTkSegmentedButton(
            header,
            values=["Base Layer (L1)", "Fn Layer (L2)"],
            command=self._on_layer_changed,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=220,
        )
        self.layer_selector.set("Base Layer (L1)")
        self.layer_selector.grid(row=0, column=1, padx=15, sticky="e")

        self.diff_badge = ctk.CTkLabel(
            header,
            text="0 modifications",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
        )
        self.diff_badge.grid(row=0, column=2, padx=5, sticky="e")

        # ---------------------------------------------------------------------
        # Left Column: Selected Physical Key Card
        # ---------------------------------------------------------------------
        self.left_col = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        self.left_col.grid(row=1, column=0, padx=(15, 8), pady=6, sticky="nsew")

        card_title = ctk.CTkLabel(
            self.left_col,
            text="Selected Physical Key",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        card_title.pack(anchor="w", padx=12, pady=(10, 6))

        self.prompt_label = ctk.CTkLabel(
            self.left_col,
            text="Click a key on the keyboard canvas\nabove to inspect and change binding.",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
            justify="left",
        )
        self.prompt_label.pack(anchor="w", padx=12, pady=4)

        # Container for key details (shown when a key is selected)
        self.info_box = ctk.CTkFrame(self.left_col, fg_color=("#d4d4d8", "#1e1e24"), corner_radius=6)
        self.info_box.pack(fill="x", padx=12, pady=4)

        self.lbl_key_identity = ctk.CTkLabel(
            self.info_box,
            text="Physical Key: None",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.lbl_key_identity.pack(anchor="w", padx=10, pady=(8, 2))

        self.lbl_key_slots = ctk.CTkLabel(
            self.info_box,
            text="Switch: - | Remap: -",
            font=ctk.CTkFont(size=10),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.lbl_key_slots.pack(anchor="w", padx=10, pady=1)

        self.lbl_default_binding = ctk.CTkLabel(
            self.info_box,
            text="Factory Default: None",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            anchor="w",
        )
        self.lbl_default_binding.pack(anchor="w", padx=10, pady=1)

        self.lbl_current_binding = ctk.CTkLabel(
            self.info_box,
            text="Current Binding: None",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
            anchor="w",
        )
        self.lbl_current_binding.pack(anchor="w", padx=10, pady=(2, 4))

        self.lbl_status_badge = ctk.CTkLabel(
            self.info_box,
            text="Status: ● Factory Default",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10b981",
            anchor="w",
        )
        self.lbl_status_badge.pack(anchor="w", padx=10, pady=(0, 8))

        # Actions frame
        actions_box = ctk.CTkFrame(self.left_col, fg_color="transparent")
        actions_box.pack(fill="x", padx=12, pady=(10, 4))

        self.btn_unbind = ctk.CTkButton(
            actions_box,
            text="Disable / Unbind Key",
            font=ctk.CTkFont(size=11),
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            height=28,
            command=self._on_unbind,
        )
        self.btn_unbind.pack(fill="x", pady=3)

        self.btn_reset_key = ctk.CTkButton(
            actions_box,
            text="Reset Key to Default",
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            height=28,
            command=self._on_reset_key,
        )
        self.btn_reset_key.pack(fill="x", pady=3)

        self.btn_reset_all = ctk.CTkButton(
            actions_box,
            text="Reset All Remaps",
            font=ctk.CTkFont(size=11),
            fg_color="#27272a",
            hover_color="#3f3f46",
            height=28,
            command=self._on_reset_all,
        )
        self.btn_reset_all.pack(fill="x", pady=(10, 3))

        # ---------------------------------------------------------------------
        # Right Column: Compact Remap Catalog
        # ---------------------------------------------------------------------
        self.right_col = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        self.right_col.grid(row=1, column=1, padx=(8, 15), pady=6, sticky="nsew")
        self.right_col.grid_columnconfigure(0, weight=1)
        self.right_col.grid_rowconfigure(2, weight=1)

        cat_header = ctk.CTkFrame(self.right_col, fg_color="transparent")
        cat_header.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="ew")
        cat_header.grid_columnconfigure(0, weight=1)

        cat_title = ctk.CTkLabel(
            cat_header,
            text="Assign New Binding (Catalog)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        cat_title.pack(side="left")

        # Category Selector
        self.cat_seg = ctk.CTkSegmentedButton(
            self.right_col,
            values=["Letters", "Numbers", "Modifiers", "Navigation", "Function", "Keypad"],
            command=self._on_category_selected,
        )
        self.cat_seg.set("Letters")
        self.cat_seg.grid(row=1, column=0, padx=12, pady=(2, 6), sticky="ew")

        # Search Bar
        search_box = ctk.CTkFrame(self.right_col, fg_color="transparent")
        search_box.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="ew")
        search_box.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            search_box,
            placeholder_text="Search key by name or hex code (e.g. Enter, F5, Ctrl, 0x04)...",
            height=28,
            font=ctk.CTkFont(size=11),
        )
        self.search_entry.pack(fill="x")
        self.search_entry.bind("<KeyRelease>", self._on_search_changed)

        # Scrollable Key Tiles
        self.catalog_scroll = ctk.CTkScrollableFrame(
            self.right_col,
            height=160,
            fg_color=("#d4d4d8", "#1e1e24"),
            corner_radius=6,
        )
        self.catalog_scroll.grid(row=3, column=0, padx=12, pady=4, sticky="nsew")
        self.right_col.grid_rowconfigure(3, weight=1)

        # Bottom Assign Action Bar
        assign_bar = ctk.CTkFrame(self.right_col, fg_color="transparent")
        assign_bar.grid(row=4, column=0, padx=12, pady=(6, 10), sticky="ew")
        assign_bar.grid_columnconfigure(0, weight=1)

        self.target_label = ctk.CTkLabel(
            assign_bar,
            text="Selected Target: (click key in catalog)",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.target_label.grid(row=0, column=0, sticky="w")

        self.btn_assign = ctk.CTkButton(
            assign_bar,
            text="Assign to Selected Key",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=180,
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._on_assign,
            state="disabled",
        )
        self.btn_assign.grid(row=0, column=1, sticky="e")

        # Initial catalog population
        self._populate_catalog()

    def _on_category_selected(self, category: str) -> None:
        self.current_category = category
        self.search_entry.delete(0, "end")
        self._populate_catalog()

    def _on_search_changed(self, event=None) -> None:
        self._populate_catalog()

    def _on_btn_hover(self, scancode: int, name: str) -> None:
        full = FULL_KEY_DESCRIPTIONS.get(scancode, name)
        self.target_label.configure(
            text=f"Hover: {full} (0x{scancode:02X})",
            text_color="#94a3b8",
        )

    def _on_btn_leave(self) -> None:
        if self.selected_target_scancode is not None:
            full = FULL_KEY_DESCRIPTIONS.get(self.selected_target_scancode, self.selected_target_name or "")
            self.target_label.configure(
                text=f"Selected Target: {full} (0x{self.selected_target_scancode:02X})",
                text_color="#38bdf8",
            )
        else:
            self.target_label.configure(
                text="Selected Target: (click key in catalog)",
                text_color="#a1a1aa",
            )

    def _populate_catalog(self) -> None:
        """Populate catalog items based on selected category and search query."""
        for widget in self.catalog_scroll.winfo_children():
            widget.destroy()

        query = self.search_entry.get().strip().lower()

        items: List[Tuple[int, str]] = []
        if query:
            # Search across all categories
            seen_scancodes = set()
            for cat_items in REMAP_CATALOG.values():
                for code, name in cat_items:
                    if code not in seen_scancodes:
                        full_desc = FULL_KEY_DESCRIPTIONS.get(code, "").lower()
                        match = (
                            query in name.lower()
                            or query in f"0x{code:02x}"
                            or query == str(code)
                            or query in full_desc
                        )
                        if match:
                            items.append((code, name))
                            seen_scancodes.add(code)
        else:
            items = REMAP_CATALOG.get(self.current_category, [])

        if not items:
            lbl_empty = ctk.CTkLabel(
                self.catalog_scroll,
                text="No keys match search query.",
                text_color="#a1a1aa",
                font=ctk.CTkFont(size=11),
            )
            lbl_empty.pack(pady=20)
            return

        if query:
            max_len = max(len(name) for _, name in items)
            if max_len <= 3:
                cols = 8
            elif max_len <= 6:
                cols = 6
            elif max_len <= 9:
                cols = 5
            else:
                cols = 4
        else:
            cols = CATEGORY_COLUMNS.get(self.current_category, 6)

        for col_idx in range(cols):
            self.catalog_scroll.grid_columnconfigure(col_idx, weight=1)

        btn_w = 46 if cols >= 8 else (58 if cols >= 6 else (78 if cols >= 5 else 98))

        for idx, (code, name) in enumerate(items):
            r = idx // cols
            c = idx % cols

            is_selected = (self.selected_target_scancode == code)
            fg = "#2563eb" if is_selected else "#27272a"
            hover = "#1d4ed8" if is_selected else "#3f3f46"

            btn = ctk.CTkButton(
                self.catalog_scroll,
                text=name,
                font=ctk.CTkFont(size=10, weight="bold"),
                width=btn_w,
                height=26,
                fg_color=fg,
                hover_color=hover,
                command=lambda c_code=code, c_name=name: self._on_target_clicked(c_code, c_name),
            )
            btn.grid(row=r, column=c, padx=2, pady=2, sticky="ew")
            btn.bind("<Enter>", lambda e, c_code=code, c_name=name: self._on_btn_hover(c_code, c_name))
            btn.bind("<Leave>", lambda e: self._on_btn_leave())

    def _on_target_clicked(self, scancode: int, name: str) -> None:
        self.selected_target_scancode = scancode
        self.selected_target_name = name
        full = FULL_KEY_DESCRIPTIONS.get(scancode, name)
        self.target_label.configure(
            text=f"Selected Target: {full} (0x{scancode:02X})",
            text_color="#38bdf8",
        )
        self._update_assign_button_state()
        self._populate_catalog()

    def _on_assign(self) -> None:
        """Assign the selected target scancode to the currently selected physical key."""
        if self.selected_target_scancode is None:
            return

        switch_slot = self.controller.selected_key_slot
        if switch_slot is None:
            return

        success = self.controller.set_key_binding(switch_slot, self.selected_target_scancode)
        if success:
            self.update_display()

    def _on_unbind(self) -> None:
        """Disable / unbind the currently selected key."""
        switch_slot = self.controller.selected_key_slot
        if switch_slot is None:
            return

        success = self.controller.unbind_key(switch_slot)
        if success:
            self.update_display()

    def _on_reset_key(self) -> None:
        """Reset the selected key to baseline default."""
        switch_slot = self.controller.selected_key_slot
        if switch_slot is None:
            return

        success = self.controller.reset_key_to_default(switch_slot)
        if success:
            self.update_display()

    def _on_reset_all(self) -> None:
        """Reset all remap changes back to baseline."""
        success = self.controller.reset_all_remap()
        if success:
            self.update_display()

    def _on_layer_changed(self, value: str) -> None:
        """Handle layer switch between Base Layer (L1) and Fn Layer (L2)."""
        layer = 2 if ("Fn" in value or "L2" in value) else 1
        self.controller.set_active_remap_layer(layer)
        self.update_display()

    def _update_assign_button_state(self) -> None:
        switch_slot = self.controller.selected_key_slot
        info = self.controller.get_key_remap_info(switch_slot) if switch_slot is not None else None

        can_assign = (
            self.selected_target_scancode is not None
            and info is not None
            and not info.is_readonly
        )
        self.btn_assign.configure(state="normal" if can_assign else "disabled")

    def update_display(self) -> None:
        """Synchronize UI with controller state."""
        active_layer = getattr(self.controller, "active_remap_layer", 1)
        expected_seg = "Base Layer (L1)" if active_layer == 1 else "Fn Layer (L2)"
        if self.layer_selector.get() != expected_seg:
            self.layer_selector.set(expected_seg)

        layer_title = "Base Layer (L1)" if active_layer == 1 else "Fn Layer (L2)"
        self.title_label.configure(text=f"Key Remapping ({layer_title})")

        switch_slot = self.controller.selected_key_slot
        info = self.controller.get_key_remap_info(switch_slot) if switch_slot is not None else None

        # 1. Update Diff Badge
        diff = self.controller.last_diff
        remap_diff_count = 0
        if diff:
            sub_name = "remap_l1" if active_layer == 1 else "remap_l2"
            sub = diff.get_subsystem(sub_name)
            if sub and sub.has_changes:
                remap_diff_count = sub.change_count

        if remap_diff_count > 0:
            self.diff_badge.configure(
                text=f"● {remap_diff_count} slot(s) modified in L{active_layer}",
                text_color="#fbbf24",
            )
            self.btn_reset_all.configure(state="normal")
        else:
            self.diff_badge.configure(
                text=f"0 modifications in L{active_layer} (Identical to baseline)",
                text_color="#a1a1aa",
            )
            self.btn_reset_all.configure(state="disabled")

        # 2. Update Selected Key Card
        if info is None:
            self.prompt_label.pack(anchor="w", padx=12, pady=4)
            self.info_box.pack_forget()
            self.btn_unbind.configure(state="disabled")
            self.btn_reset_key.configure(state="disabled")
        else:
            self.prompt_label.pack_forget()
            self.info_box.pack(fill="x", padx=12, pady=4)

            key_name = (
                info.label
                if info.label.strip().upper() == info.key_id.strip().upper()
                else f"{info.label} ({info.key_id})"
            )
            self.lbl_key_identity.configure(
                text=f"Physical Key: {key_name}"
            )
            self.lbl_key_slots.configure(
                text=f"Switch Slot: {info.switch_slot} (0x{info.switch_slot:02X})  |  Remap Slot: {info.remap_slot} (0x{info.remap_slot:02X})"
            )
            self.lbl_default_binding.configure(
                text=f"Factory Default: {info.default_name}"
            )

            # Binding color and label
            if info.is_unbound:
                self.lbl_current_binding.configure(
                    text="Current Binding: [Disabled / Unbound]",
                    text_color="#ef4444",
                )
            elif info.is_modified:
                self.lbl_current_binding.configure(
                    text=f"Current Binding: {info.current_name}",
                    text_color="#38bdf8",
                )
            else:
                self.lbl_current_binding.configure(
                    text=f"Current Binding: {info.current_name}",
                    text_color="#f4f4f5",
                )

            # Status badge
            if info.is_readonly:
                if info.switch_slot in FN_DISABLED_SWITCH_SLOTS:
                    self.lbl_status_badge.configure(
                        text="Status: 🔒 Fn Layer Protected (F1..F12 media lock)",
                        text_color="#f87171",
                    )
                else:
                    self.lbl_status_badge.configure(
                        text="Status: 🔒 System Reserved (Fn Key locked)",
                        text_color="#f87171",
                    )
            elif info.is_unbound:
                self.lbl_status_badge.configure(
                    text="Status: ● Disabled (Silent)",
                    text_color="#ef4444",
                )
            elif info.is_modified:
                self.lbl_status_badge.configure(
                    text="Status: ● Modified (Pending Apply)",
                    text_color="#fbbf24",
                )
            else:
                self.lbl_status_badge.configure(
                    text="Status: ● Factory Default",
                    text_color="#10b981",
                )

            # Button states
            if info.is_readonly:
                self.btn_unbind.configure(state="disabled")
                self.btn_reset_key.configure(state="disabled")
            else:
                self.btn_unbind.configure(
                    state="disabled" if info.is_unbound else "normal"
                )
                self.btn_reset_key.configure(
                    state="normal" if info.is_modified else "disabled"
                )

        self._update_assign_button_state()
