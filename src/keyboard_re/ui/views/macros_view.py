"""
Macro Editor View for IO by Red Square Type 84 Configurator.

Provides interactive controls for creating, editing, and deleting hardware macros:
- Lists defined macros (0..99).
- Edits action sequence: keycodes, press/release events, and millisecond delays.
- Two-stage wire image generation (catalog at 0x0000 + heap at 0x0190).
"""

from __future__ import annotations

from typing import Dict, List, Optional
import customtkinter as ctk

from keyboard_re.protocol.keymap import HID_USAGE_NAMES
from keyboard_re.protocol.macro import MacroAction, MacroActionType, MacroDefinition
from keyboard_re.ui.controller import AppController

# Common keys for quick selection
COMMON_MACRO_KEYS: List[tuple[str, int]] = [
    ("W", 0x1A), ("A", 0x04), ("S", 0x16), ("D", 0x07),
    ("Q", 0x14), ("E", 0x08), ("R", 0x15), ("F", 0x09),
    ("Space", 0x2C), ("Enter", 0x28), ("Tab", 0x2B),
    ("Escape", 0x29), ("Backspace", 0x2A),
    ("Left Shift", 0xE1), ("Left Ctrl", 0xE0), ("Left Alt", 0xE2),
    ("1", 0x1E), ("2", 0x1F), ("3", 0x20), ("4", 0x21),
]


class MacrosView(ctk.CTkFrame):
    """View panel for managing and editing hardware macros."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller
        self.selected_macro_id: Optional[int] = None
        self._current_actions: List[MacroAction] = []

        self.grid_columnconfigure(0, weight=0, minsize=240)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Hardware Macro Editor (AA 15 / AA 25)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        # ---------------------------------------------------------------------
        # Left Panel: Macro List & Management
        # ---------------------------------------------------------------------
        left_frame = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        left_frame.grid(row=1, column=0, padx=(15, 7), pady=(0, 15), sticky="nsew")
        left_frame.grid_rowconfigure(1, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            left_frame,
            text="Saved Macros:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

        self.macro_list_scroll = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
        self.macro_list_scroll.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

        btn_row = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self.new_btn = ctk.CTkButton(
            btn_row,
            text="+ New",
            width=70,
            command=self._on_new_macro,
        )
        self.new_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self.del_btn = ctk.CTkButton(
            btn_row,
            text="Delete",
            width=70,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            state="disabled",
            command=self._on_delete_macro,
        )
        self.del_btn.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # ---------------------------------------------------------------------
        # Right Panel: Macro Action Sequence Editor
        # ---------------------------------------------------------------------
        self.right_frame = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        self.right_frame.grid(row=1, column=1, padx=(7, 15), pady=(0, 15), sticky="nsew")
        self.right_frame.grid_rowconfigure(2, weight=1)
        self.right_frame.grid_columnconfigure(0, weight=1)

        # Header Info Row
        info_row = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        info_row.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="ew")
        info_row.grid_columnconfigure(1, weight=1)

        self.macro_id_label = ctk.CTkLabel(
            info_row,
            text="Macro Slot: (none)",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.macro_id_label.grid(row=0, column=0, padx=(0, 10), sticky="w")

        self.macro_name_entry = ctk.CTkEntry(
            info_row,
            placeholder_text="Macro Name",
            height=28,
        )
        self.macro_name_entry.grid(row=0, column=1, padx=(0, 10), sticky="ew")

        # Action List Frame
        self.action_scroll = ctk.CTkScrollableFrame(self.right_frame, fg_color="transparent")
        self.action_scroll.grid(row=2, column=0, padx=15, pady=5, sticky="nsew")

        # Action Addition Controls Row
        add_row = ctk.CTkFrame(self.right_frame, fg_color=("#d4d4d8", "#3f3f46"), corner_radius=6)
        add_row.grid(row=3, column=0, padx=15, pady=(5, 10), sticky="ew")

        ctk.CTkLabel(add_row, text="Key:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(10, 5))
        self.key_menu = ctk.CTkOptionMenu(
            add_row,
            values=[name for name, _ in COMMON_MACRO_KEYS],
            width=110,
        )
        self.key_menu.pack(side="left", padx=5)

        ctk.CTkLabel(add_row, text="Event:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(10, 5))
        self.event_menu = ctk.CTkOptionMenu(
            add_row,
            values=["Tap (Down+Up)", "Press Only", "Release Only"],
            width=130,
        )
        self.event_menu.pack(side="left", padx=5)

        ctk.CTkLabel(add_row, text="Delay (ms):", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(10, 5))
        self.delay_entry = ctk.CTkEntry(add_row, width=60, placeholder_text="50")
        self.delay_entry.insert(0, "50")
        self.delay_entry.pack(side="left", padx=5)

        self.add_action_btn = ctk.CTkButton(
            add_row,
            text="+ Add Event",
            width=90,
            command=self._on_add_action,
        )
        self.add_action_btn.pack(side="left", padx=(10, 10), pady=8)

        # Footer Action Row
        footer_row = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        footer_row.grid(row=4, column=0, padx=15, pady=(5, 15), sticky="ew")

        self.clear_actions_btn = ctk.CTkButton(
            footer_row,
            text="Clear Actions",
            width=100,
            fg_color="#52525b",
            hover_color="#71717a",
            command=self._on_clear_actions,
        )
        self.clear_actions_btn.pack(side="left")

        self.save_macro_btn = ctk.CTkButton(
            footer_row,
            text="Save to Working Profile",
            width=170,
            fg_color="#16a34a",
            hover_color="#15803d",
            command=self._on_save_macro,
        )
        self.save_macro_btn.pack(side="right")

    def update_display(self) -> None:
        """Refresh macro list and selected macro actions."""
        # Clear macro list buttons
        for widget in self.macro_list_scroll.winfo_children():
            widget.destroy()

        macros = self.controller.get_macro_list()

        if not macros:
            lbl = ctk.CTkLabel(
                self.macro_list_scroll,
                text="No macros defined.\nClick '+ New' to create one.",
                font=ctk.CTkFont(size=11),
                text_color="#71717a",
                justify="center",
            )
            lbl.pack(pady=20)
            self.del_btn.configure(state="disabled")
            self._render_empty_editor()
            return

        # Render macro buttons
        for m in macros:
            is_sel = (m.macro_id == self.selected_macro_id)
            btn = ctk.CTkButton(
                self.macro_list_scroll,
                text=f"[{m.macro_id}] {m.name or f'Macro {m.macro_id}'} ({len(m.actions)} ev)",
                fg_color="#2563eb" if is_sel else ("#d4d4d8", "#3f3f46"),
                hover_color="#1d4ed8" if is_sel else ("#cbd5e1", "#52525b"),
                text_color="#ffffff" if is_sel else ("#09090b", "#f4f4f5"),
                anchor="w",
                command=lambda mid=m.macro_id: self._select_macro(mid),
            )
            btn.pack(fill="x", pady=2)

        if self.selected_macro_id is None and macros:
            self._select_macro(macros[0].macro_id)
        else:
            self._refresh_editor()

    def _select_macro(self, macro_id: int) -> None:
        self.selected_macro_id = macro_id
        self.del_btn.configure(state="normal")
        macro = self.controller.get_macro(macro_id)
        if macro:
            self._current_actions = [a.clone() for a in macro.actions]
            self.macro_name_entry.delete(0, "end")
            self.macro_name_entry.insert(0, macro.name or f"Macro {macro_id}")
        else:
            self._current_actions = []
        self._refresh_editor()
        self.update_display()

    def _render_empty_editor(self) -> None:
        self.macro_id_label.configure(text="Macro Slot: (none)")
        self.macro_name_entry.delete(0, "end")
        for widget in self.action_scroll.winfo_children():
            widget.destroy()
        lbl = ctk.CTkLabel(
            self.action_scroll,
            text="Select or create a macro to start editing actions.",
            font=ctk.CTkFont(size=12),
            text_color="#71717a",
        )
        lbl.pack(pady=40)

    def _refresh_editor(self) -> None:
        if self.selected_macro_id is None:
            self._render_empty_editor()
            return

        self.macro_id_label.configure(text=f"Macro Slot #{self.selected_macro_id}:")

        for widget in self.action_scroll.winfo_children():
            widget.destroy()

        if not self._current_actions:
            lbl = ctk.CTkLabel(
                self.action_scroll,
                text="No actions in this macro.\nUse the '+ Add Event' controls below to add key events.",
                font=ctk.CTkFont(size=12),
                text_color="#71717a",
            )
            lbl.pack(pady=30)
            return

        for idx, act in enumerate(self._current_actions):
            row = ctk.CTkFrame(self.action_scroll, fg_color=("white", "#18181b"), corner_radius=4)
            row.pack(fill="x", pady=2, padx=2)

            key_name = HID_USAGE_NAMES.get(act.key_code, f"Key 0x{act.key_code:02X}")
            ev_str = "PRESS" if act.is_press else "RELEASE"
            ev_color = "#16a34a" if act.is_press else "#dc2626"

            ctk.CTkLabel(
                row,
                text=f"#{idx + 1:02d}",
                width=30,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=(8, 4))

            ctk.CTkLabel(
                row,
                text=ev_str,
                width=60,
                text_color=ev_color,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=4)

            ctk.CTkLabel(
                row,
                text=f"{key_name} (0x{act.key_code:02X})",
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).pack(side="left", padx=8, expand=True, fill="x")

            ctk.CTkLabel(
                row,
                text=f"Delay: {act.delay} ms",
                font=ctk.CTkFont(size=11),
                width=100,
            ).pack(side="left", padx=8)

            del_action_btn = ctk.CTkButton(
                row,
                text="✕",
                width=24,
                height=20,
                fg_color="transparent",
                hover_color=("#fecaca", "#450a0a"),
                text_color="#ef4444",
                command=lambda i=idx: self._remove_action(i),
            )
            del_action_btn.pack(side="right", padx=(4, 8))

    def _remove_action(self, index: int) -> None:
        if 0 <= index < len(self._current_actions):
            self._current_actions.pop(index)
            self._refresh_editor()

    def _on_new_macro(self) -> None:
        existing_ids = {m.macro_id for m in self.controller.get_macro_list()}
        new_id = 0
        while new_id in existing_ids and new_id < 100:
            new_id += 1
        if new_id >= 100:
            return

        new_macro = MacroDefinition(macro_id=new_id, name=f"Macro {new_id}", actions=[])
        self.controller.save_macro(new_macro)
        self._select_macro(new_id)

    def _on_delete_macro(self) -> None:
        if self.selected_macro_id is not None:
            self.controller.delete_macro(self.selected_macro_id)
            self.selected_macro_id = None
            self.update_display()

    def _on_add_action(self) -> None:
        key_name = self.key_menu.get()
        key_code = 0x1A  # Default 'W'
        for name, sc in COMMON_MACRO_KEYS:
            if name == key_name:
                key_code = sc
                break

        try:
            delay = int(self.delay_entry.get().strip())
        except ValueError:
            delay = 50

        ev_type = self.event_menu.get()

        if "Tap" in ev_type:
            # Add Press (delay) + Release (50ms)
            self._current_actions.append(MacroAction(action_type=1, is_press=True, key_code=key_code, delay=delay))
            self._current_actions.append(MacroAction(action_type=1, is_press=False, key_code=key_code, delay=50))
        elif "Press" in ev_type:
            self._current_actions.append(MacroAction(action_type=1, is_press=True, key_code=key_code, delay=delay))
        else:
            self._current_actions.append(MacroAction(action_type=1, is_press=False, key_code=key_code, delay=delay))

        self._refresh_editor()

    def _on_clear_actions(self) -> None:
        self._current_actions = []
        self._refresh_editor()

    def _on_save_macro(self) -> None:
        if self.selected_macro_id is None:
            return
        name = self.macro_name_entry.get().strip() or f"Macro {self.selected_macro_id}"
        macro_def = MacroDefinition(
            macro_id=self.selected_macro_id,
            name=name,
            actions=[a.clone() for a in self._current_actions],
        )
        self.controller.save_macro(macro_def)
        self.update_display()
