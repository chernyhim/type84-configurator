"""
RGB Global Effects Editor View for IO by Red Square Type 84 Configurator.

Provides interactive controls for:
- 26 RGB Effects from declarative RGB_EFFECT_CATALOG
- Brightness level (0..5)
- Animation speed (0..5)
- Color Mode (Single Color vs Rainbow RGB)
- Primary Color palette & Hex input
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import customtkinter as ctk

from keyboard_re.protocol.rgb import RGBEffectMeta
from keyboard_re.ui.controller import AppController


class RGBGlobalView(ctk.CTkFrame):
    """View panel for configuring Global RGB parameters."""

    PRESET_COLORS = [
        ("#FF0000", "Red"),
        ("#FF7700", "Orange"),
        ("#FFFF00", "Yellow"),
        ("#00FF00", "Green"),
        ("#00FFFF", "Cyan"),
        ("#0066FF", "Blue"),
        ("#9900FF", "Purple"),
        ("#FFFFFF", "White"),
    ]

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller

        self._effects_list = self.controller.get_rgb_effects()
        self._effect_by_display: Dict[str, int] = {
            display: effect_id for effect_id, display, _ in self._effects_list
        }
        self._display_by_effect: Dict[int, str] = {
            effect_id: display for effect_id, display, _ in self._effects_list
        }
        self._meta_by_effect: Dict[int, RGBEffectMeta] = {
            effect_id: meta for effect_id, _, meta in self._effects_list
        }

        self.grid_columnconfigure(0, weight=1, uniform="global_cols")
        self.grid_columnconfigure(1, weight=1, uniform="global_cols")

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="RGB Global Lighting Effects",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        # 1. Effect Selector
        self.effect_label = ctk.CTkLabel(
            self,
            text="Lighting Effect:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.effect_label.grid(row=1, column=0, padx=15, pady=(5, 2), sticky="w")

        effect_names = [display for _, display, _ in self._effects_list]
        self.effect_menu = ctk.CTkOptionMenu(
            self,
            values=effect_names,
            width=320,
            command=self._on_effect_selected,
        )
        self.effect_menu.grid(row=2, column=0, padx=15, pady=(0, 10), sticky="w")

        # 2. Brightness Slider
        self.brightness_label = ctk.CTkLabel(
            self,
            text="Brightness: 5",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.brightness_label.grid(row=3, column=0, padx=15, pady=(5, 2), sticky="w")

        self.brightness_slider = ctk.CTkSlider(
            self,
            from_=0,
            to=5,
            number_of_steps=5,
            width=320,
            command=self._on_brightness_changed,
        )
        self.brightness_slider.grid(row=4, column=0, padx=15, pady=(0, 10), sticky="w")

        # 3. Speed Slider
        self.speed_label = ctk.CTkLabel(
            self,
            text="Speed: 5",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.speed_label.grid(row=5, column=0, padx=15, pady=(5, 2), sticky="w")

        self.speed_slider = ctk.CTkSlider(
            self,
            from_=0,
            to=5,
            number_of_steps=5,
            width=320,
            command=self._on_speed_changed,
        )
        self.speed_slider.grid(row=6, column=0, padx=15, pady=(0, 15), sticky="w")

        # Column 1: Color Controls
        # 4. Color Mode (Single Color vs Rainbow RGB)
        self.color_mode_label = ctk.CTkLabel(
            self,
            text="Color Mode:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.color_mode_label.grid(row=1, column=1, padx=15, pady=(5, 2), sticky="w")

        self.color_mode_segment = ctk.CTkSegmentedButton(
            self,
            values=["Single Color", "Rainbow RGB"],
            width=280,
            dynamic_resizing=False,
            command=self._on_color_mode_changed,
        )
        self.color_mode_segment.grid(row=2, column=1, padx=15, pady=(0, 10), sticky="w")

        # 5. Primary Color Selection
        self.palette_label = ctk.CTkLabel(
            self,
            text="Primary Color:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.palette_label.grid(row=3, column=1, padx=15, pady=(5, 2), sticky="w")

        # Preset palette buttons container
        self.palette_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.palette_frame.grid(row=4, column=1, padx=15, pady=(0, 10), sticky="w")

        for idx, (color_hex, name) in enumerate(self.PRESET_COLORS):
            btn = ctk.CTkButton(
                self.palette_frame,
                text="",
                width=32,
                height=26,
                fg_color=color_hex,
                hover_color=color_hex,
                border_width=1,
                border_color="#52525b",
                command=lambda c=color_hex: self._on_color_picked(c),
            )
            btn.grid(row=0, column=idx, padx=3, pady=2)

        # Hex code entry
        self.hex_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.hex_frame.grid(row=5, column=1, padx=15, pady=(0, 10), sticky="w")

        self.hex_entry = ctk.CTkEntry(self.hex_frame, width=90, font=ctk.CTkFont(size=12))
        self.hex_entry.grid(row=0, column=0, padx=(0, 6))
        self.hex_entry.insert(0, "#FFFFFF")

        self.hex_apply_btn = ctk.CTkButton(
            self.hex_frame,
            text="Set Hex",
            width=65,
            command=self._on_hex_apply,
        )
        self.hex_apply_btn.grid(row=0, column=1, padx=(0, 6))

        self.color_picker_btn = ctk.CTkButton(
            self.hex_frame,
            text="Pick Color...",
            width=95,
            fg_color="#4f46e5",
            hover_color="#4338ca",
            command=self._on_pick_color,
        )
        self.color_picker_btn.grid(row=0, column=2)

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _on_pick_color(self) -> None:
        """Open standard system color chooser dialog and apply selected color."""
        import tkinter.colorchooser as cc
        initial_color = self.hex_entry.get().strip() or "#FFFFFF"
        result = cc.askcolor(color=initial_color, title="Select Primary Lighting Color", parent=self)
        if result and result[1]:
            chosen_hex = str(result[1]).upper()
            self.hex_entry.delete(0, "end")
            self.hex_entry.insert(0, chosen_hex)
            self.controller.set_rgb_primary_color(chosen_hex)

    def _on_effect_selected(self, choice: str) -> None:
        effect_id = self._effect_by_display.get(choice)
        if effect_id is not None:
            self.controller.set_rgb_effect(effect_id)

    def _on_brightness_changed(self, value: float) -> None:
        int_val = int(round(value))
        self.brightness_label.configure(text=f"Brightness: {int_val}")
        self.controller.set_rgb_brightness(int_val)

    def _on_speed_changed(self, value: float) -> None:
        int_val = int(round(value))
        self.speed_label.configure(text=f"Speed: {int_val}")
        self.controller.set_rgb_speed(int_val)

    def _on_color_mode_changed(self, choice: str) -> None:
        mode = 0 if choice == "Single Color" else 1
        self.controller.set_rgb_color_mode(mode)

    def _on_color_picked(self, color_hex: str) -> None:
        self.hex_entry.delete(0, "end")
        self.hex_entry.insert(0, color_hex)
        self.controller.set_rgb_primary_color(color_hex)

    def _on_hex_apply(self) -> None:
        val = self.hex_entry.get().strip()
        if not val.startswith("#"):
            val = "#" + val
        try:
            self.controller.set_rgb_primary_color(val)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # View Synchronization
    # -------------------------------------------------------------------------

    def update_display(self) -> None:
        """Update widgets from controller working profile state."""
        current = self.controller.get_current_rgb()
        if not current:
            return

        effect_id = current["effect"]
        meta = current.get("meta")
        display_name = self._display_by_effect.get(
            effect_id,
            meta.name_en if meta else f"Unknown Effect (0x{effect_id:02X})",
        )
        if display_name and self.effect_menu.get() != display_name:
            self.effect_menu.set(display_name)

        # Brightness
        b_val = current["brightness"]
        if self.brightness_slider.get() != b_val:
            self.brightness_slider.set(b_val)
        self.brightness_label.configure(text=f"Brightness: {b_val}")

        # Speed
        s_val = current["speed"]
        if self.speed_slider.get() != s_val:
            self.speed_slider.set(s_val)
        self.speed_label.configure(text=f"Speed: {s_val}")

        # Color Mode
        c_mode = current["color_mode"]
        target_mode_str = "Single Color" if c_mode == 0 else "Rainbow RGB"
        if self.color_mode_segment.get() != target_mode_str:
            self.color_mode_segment.set(target_mode_str)

        # Primary Hex
        p_hex = current["primary_hex"]
        if self.hex_entry.get() != p_hex:
            self.hex_entry.delete(0, "end")
            self.hex_entry.insert(0, p_hex)

        # Feature availability based on effect metadata
        if meta:
            target_speed_state = "normal" if meta.has_speed else "disabled"
            if self.speed_slider.cget("state") != target_speed_state:
                self.speed_slider.configure(state=target_speed_state)

            target_color_mode_state = "normal" if meta.has_color_mode else "disabled"
            if self.color_mode_segment.cget("state") != target_color_mode_state:
                self.color_mode_segment.configure(state=target_color_mode_state)
