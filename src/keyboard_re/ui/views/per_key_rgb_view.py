"""
Per-Key RGB Editor View for IO by Red Square Type 84 Configurator.

Provides interactive controls for:
- Custom Mode (Effect 0x80) status and activation
- Active painting color preview, hex input, and color picker
- Eyedropper / Color Sampling from selected keys
- 10-color quick palette
- Matrix operations: Apply to selected, Clear selected, Fill all, Clear all
- Preset key selection groups: All 84, WASD, Arrows, Modifiers, Deselect
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import colorchooser
from typing import Optional, Tuple
import customtkinter as ctk

from keyboard_re.protocol.rgb import EFFECT_CUSTOM
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT


class PerKeyRGBView(ctk.CTkFrame):
    """View panel for Custom Per-Key RGB lighting matrix configuration."""

    PRESET_PALETTE = [
        ("#FF0000", "Red"),
        ("#FF7700", "Orange"),
        ("#FFFF00", "Yellow"),
        ("#00FF00", "Green"),
        ("#00FFFF", "Cyan"),
        ("#0066FF", "Blue"),
        ("#9900FF", "Purple"),
        ("#FF007F", "Pink"),
        ("#FFFFFF", "White"),
        ("#000000", "Off"),
    ]

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller

        # Active painting color (defaults to Red)
        self.active_color: str = "#FF0000"

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # 1. Header & Custom Mode Status
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, columnspan=2, padx=15, pady=(12, 6), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header_frame,
            text="Custom Per-Key RGB Matrix (Effect 0x80)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        title.grid(row=0, column=0, sticky="w")

        self.mode_badge = ctk.CTkLabel(
            header_frame,
            text="Mode: Checking...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8",
        )
        self.mode_badge.grid(row=0, column=1, padx=(10, 10), sticky="e")

        self.activate_mode_btn = ctk.CTkButton(
            header_frame,
            text="Activate Custom Mode",
            font=ctk.CTkFont(size=11),
            width=150,
            height=26,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_activate_custom_mode,
        )
        self.activate_mode_btn.grid(row=0, column=2, sticky="e")

        # ---------------------------------------------------------------------
        # Left Column: Selection Controls & Quick Presets
        # ---------------------------------------------------------------------
        left_col = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        left_col.grid(row=1, column=0, padx=(15, 8), pady=6, sticky="nsew")

        sel_title = ctk.CTkLabel(
            left_col,
            text="Key Selection Presets",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        sel_title.pack(anchor="w", padx=12, pady=(10, 6))

        # Selection action buttons
        sel_btn_box = ctk.CTkFrame(left_col, fg_color="transparent")
        sel_btn_box.pack(fill="x", padx=12, pady=4)

        b_all = ctk.CTkButton(
            sel_btn_box,
            text="Select All (84)",
            width=100,
            height=28,
            command=self.controller.select_all_leds,
        )
        b_all.pack(side="left", padx=(0, 4))

        b_desel = ctk.CTkButton(
            sel_btn_box,
            text="Deselect All",
            width=85,
            height=28,
            fg_color="#475569",
            hover_color="#64748b",
            command=self.controller.deselect_all_leds,
        )
        b_desel.pack(side="left", padx=4)

        b_wasd = ctk.CTkButton(
            sel_btn_box,
            text="WASD",
            width=65,
            height=28,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=lambda: self.controller.select_led_group("wasd"),
        )
        b_wasd.pack(side="left", padx=4)

        b_arr = ctk.CTkButton(
            sel_btn_box,
            text="Arrows",
            width=65,
            height=28,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=lambda: self.controller.select_led_group("arrows"),
        )
        b_arr.pack(side="left", padx=4)

        b_mod = ctk.CTkButton(
            sel_btn_box,
            text="Modifiers",
            width=75,
            height=28,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=lambda: self.controller.select_led_group("modifiers"),
        )
        b_mod.pack(side="left", padx=4)

        # Selection Info line
        self.selection_status_label = ctk.CTkLabel(
            left_col,
            text="Selected keys: 0 of 84",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.selection_status_label.pack(anchor="w", padx=12, pady=(8, 10))

        # ---------------------------------------------------------------------
        # Right Column: Color Palette & Matrix Operations
        # ---------------------------------------------------------------------
        right_col = ctk.CTkFrame(self, fg_color=("#e4e4e7", "#27272a"), corner_radius=6)
        right_col.grid(row=1, column=1, padx=(8, 15), pady=6, sticky="nsew")

        pal_title = ctk.CTkLabel(
            right_col,
            text="Color Palette & Paint Tools",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        pal_title.pack(anchor="w", padx=12, pady=(10, 6))

        # Active Color Control Row
        active_box = ctk.CTkFrame(right_col, fg_color="transparent")
        active_box.pack(fill="x", padx=12, pady=4)

        self.color_swatch = tk.Label(
            active_box,
            bg=self.active_color,
            width=4,
            height=1,
            relief="solid",
            bd=1,
        )
        self.color_swatch.pack(side="left", padx=(0, 8))

        self.hex_entry = ctk.CTkEntry(
            active_box,
            width=90,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.hex_entry.insert(0, self.active_color)
        self.hex_entry.pack(side="left", padx=(0, 6))
        self.hex_entry.bind("<Return>", lambda e: self._on_hex_entered())

        set_hex_btn = ctk.CTkButton(
            active_box,
            text="Set",
            width=45,
            height=28,
            command=self._on_hex_entered,
        )
        set_hex_btn.pack(side="left", padx=(0, 6))

        pick_btn = ctk.CTkButton(
            active_box,
            text="Pick Color...",
            width=95,
            height=28,
            command=self._on_pick_color,
        )
        pick_btn.pack(side="left", padx=(0, 6))

        # Eyedropper / Sample Color tool
        self.eyedropper_btn = ctk.CTkButton(
            active_box,
            text="Sample Key 🧪",
            width=100,
            height=28,
            fg_color="#475569",
            hover_color="#64748b",
            command=self._on_sample_color,
        )
        self.eyedropper_btn.pack(side="left")

        # Quick Palette Buttons
        palette_box = ctk.CTkFrame(right_col, fg_color="transparent")
        palette_box.pack(fill="x", padx=12, pady=8)

        for hex_code, name in self.PRESET_PALETTE:
            b = ctk.CTkButton(
                palette_box,
                text="",
                width=24,
                height=24,
                corner_radius=4,
                fg_color=hex_code,
                hover_color=hex_code,
                border_width=1,
                border_color="#52525b",
                command=lambda c=hex_code: self._on_palette_color_clicked(c),
            )
            b.pack(side="left", padx=3)

        # Operations buttons row
        ops_box = ctk.CTkFrame(self, fg_color="transparent")
        ops_box.grid(row=2, column=0, columnspan=2, padx=15, pady=(8, 12), sticky="ew")
        ops_box.grid_columnconfigure(0, weight=1)

        btn_container = ctk.CTkFrame(ops_box, fg_color="transparent")
        btn_container.pack(anchor="e")

        apply_sel_btn = ctk.CTkButton(
            btn_container,
            text="Apply Color to Selected",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            width=170,
            command=self._on_apply_to_selected,
        )
        apply_sel_btn.pack(side="left", padx=4)

        clear_sel_btn = ctk.CTkButton(
            btn_container,
            text="Turn Off Selected",
            fg_color="#3f3f46",
            hover_color="#52525b",
            width=135,
            command=self._on_clear_selected,
        )
        clear_sel_btn.pack(side="left", padx=4)

        fill_all_btn = ctk.CTkButton(
            btn_container,
            text="Fill All (84)",
            fg_color="#3f3f46",
            hover_color="#52525b",
            width=110,
            command=self._on_fill_all,
        )
        fill_all_btn.pack(side="left", padx=4)

        clear_all_btn = ctk.CTkButton(
            btn_container,
            text="Turn Off All",
            fg_color="#b91c1c",
            hover_color="#991b1b",
            width=110,
            command=self._on_clear_all,
        )
        clear_all_btn.pack(side="left", padx=(4, 0))

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _on_activate_custom_mode(self) -> None:
        """Activate Custom Per-Key lighting mode (0x80)."""
        self.controller.activate_custom_per_key_mode()
        self.update_display()

    def _set_active_color(self, hex_code: str) -> None:
        """Set active painting color and update UI widgets."""
        hex_clean = hex_code.strip().upper()
        if not hex_clean.startswith("#"):
            hex_clean = "#" + hex_clean
        if re.fullmatch(r"#[0-9A-F]{6}", hex_clean):
            self.active_color = hex_clean
            self.color_swatch.configure(bg=self.active_color)
            self.hex_entry.delete(0, "end")
            self.hex_entry.insert(0, self.active_color)

    def _on_hex_entered(self) -> None:
        val = self.hex_entry.get().strip()
        self._set_active_color(val)

    def _on_pick_color(self) -> None:
        """Open system color chooser dialog."""
        picked = colorchooser.askcolor(
            color=self.active_color,
            title="Pick Per-Key RGB Color",
            parent=self.winfo_toplevel(),
        )
        if picked and picked[1]:
            self._set_active_color(picked[1])

    def _on_sample_color(self) -> None:
        """Sample color from selected key (Eyedropper)."""
        sampled = self.controller.sample_color_from_selected()
        if sampled is not None:
            hex_c = f"#{sampled[0]:02X}{sampled[1]:02X}{sampled[2]:02X}"
            self._set_active_color(hex_c)

    def _on_palette_color_clicked(self, hex_code: str) -> None:
        """Clicking a palette swatch sets active color and immediately paints selected keys."""
        self._set_active_color(hex_code)
        if self.controller.selected_led_slots:
            self.controller.set_selected_leds_color(self.active_color)

    def _on_apply_to_selected(self) -> None:
        self._on_hex_entered()
        self.controller.set_selected_leds_color(self.active_color)

    def _on_clear_selected(self) -> None:
        self.controller.clear_selected_leds()

    def _on_fill_all(self) -> None:
        self._on_hex_entered()
        self.controller.fill_all_leds(self.active_color)

    def _on_clear_all(self) -> None:
        self.controller.clear_all_leds()

    # -------------------------------------------------------------------------
    # State Synchronization
    # -------------------------------------------------------------------------

    def update_display(self) -> None:
        """Refresh panel state from controller."""
        profile = self.controller.working_profile
        is_custom = bool(
            profile
            and profile.rgb_global
            and profile.rgb_global.effect == EFFECT_CUSTOM
        )

        if is_custom:
            self.mode_badge.configure(
                text="● Custom Mode (0x80) Active",
                text_color="#22c55e",
            )
            self.activate_mode_btn.configure(state="disabled", text="Custom Mode Active")
        else:
            eff_name = "None"
            if profile and profile.rgb_global and profile.rgb_global.meta:
                eff_name = profile.rgb_global.meta.name_en
            self.mode_badge.configure(
                text=f"Current: {eff_name}",
                text_color="#f59e0b",
            )
            self.activate_mode_btn.configure(state="normal", text="Switch to Custom (0x80)")

        # Update selection label
        sel_count = len(self.controller.selected_led_slots)
        if sel_count == 1:
            slot = next(iter(self.controller.selected_led_slots))
            k_def = KEY_BY_LED_SLOT.get(slot)
            name = f"'{k_def.label}'" if k_def else f"LED {slot}"
            self.selection_status_label.configure(
                text=f"Selected: 1 key ({name}, slot {slot})",
                text_color="#f4f4f5",
            )
        elif sel_count > 1:
            self.selection_status_label.configure(
                text=f"Selected: {sel_count} of 84 keys",
                text_color="#38bdf8",
            )
        else:
            self.selection_status_label.configure(
                text="Selected: 0 keys (click keys on layout)",
                text_color="#a1a1aa",
            )
