"""
Interactive 75% Keyboard Canvas View for IO by Red Square Type 84.

Renders 84 physical keys on an interactive Tkinter Canvas.
Supports mouse selection of keys with visual highlight.
"""

from __future__ import annotations

import tkinter as tk
from typing import Dict, Optional, Tuple
import customtkinter as ctk

from keyboard_re.protocol.keymap import HID_USAGE_NAMES
from keyboard_re.protocol.rgb import EFFECT_CUSTOM
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import (
    KEY_BY_LED_SLOT,
    KEY_BY_SWITCH_SLOT,
    TYPE84_LAYOUT,
    KeyDefinition,
)


def _format_remap_badge(name: str) -> str:
    """Format key binding into a compact badge string (<= 6 chars)."""
    short_forms = {
        "Left Ctrl": "Ctrl",
        "Right Ctrl": "Ctrl",
        "L-Ctrl": "Ctrl",
        "R-Ctrl": "Ctrl",
        "Left Shift": "Shft",
        "Right Shift": "Shft",
        "L-Shift": "Shft",
        "R-Shift": "Shft",
        "Left Alt": "Alt",
        "Right Alt": "Alt",
        "L-Alt": "Alt",
        "R-Alt": "Alt",
        "Left Win (GUI)": "Win",
        "Right Win (GUI)": "Win",
        "L-Win": "Win",
        "R-Win": "Win",
        "CapsLock": "Caps",
        "Caps Lock": "Caps",
        "Backspace": "Back",
        "Space": "Spc",
        "Spacebar": "Spc",
        "PageUp": "PgUp",
        "PageDown": "PgDn",
        "Page Up": "PgUp",
        "Page Down": "PgDn",
        "PrintScreen": "Prt",
        "Print Screen": "Prt",
        "ScrollLock": "Scr",
        "Scroll Lock": "Scr",
        "Delete": "Del",
        "Insert": "Ins",
        "NumLock": "Num",
        "KP Enter": "Ent",
        "KPEnter": "Ent",
        "KP /": "KP/",
        "KP *": "KP*",
        "KP -": "KP-",
        "KP +": "KP+",
        "KP .": "KP.",
    }
    cleaned = short_forms.get(name, name)
    if cleaned.startswith("↑"):
        cleaned = "↑"
    elif cleaned.startswith("↓"):
        cleaned = "↓"
    elif cleaned.startswith("←"):
        cleaned = "←"
    elif cleaned.startswith("→"):
        cleaned = "→"
    elif len(cleaned) > 5:
        cleaned = cleaned[:4]
    return f"→ {cleaned}"


class KeyboardView(ctk.CTkFrame):
    """Interactive visual representation of the 75% Type 84 keyboard layout."""

    # Visual configuration constants
    U_SIZE = 48.0        # Pixels per standard 1u key
    KEY_PADDING = 3.0    # Gap between key caps
    CANVAS_MARGIN = 12.0 # Margin around keyboard plate

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=("#f4f4f5", "#18181b"), corner_radius=8, **kwargs)
        self.controller = controller

        # Calculate canvas dimensions based on layout bounds
        max_u_x = max(k.x + k.width for k in TYPE84_LAYOUT)
        max_u_y = max(k.y + k.height for k in TYPE84_LAYOUT)
        self.canvas_width = int(max_u_x * self.U_SIZE + 2 * self.CANVAS_MARGIN)
        self.canvas_height = int(max_u_y * self.U_SIZE + 2 * self.CANVAS_MARGIN)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Tkinter Canvas for lightweight, fast rendering
        self.canvas = tk.Canvas(
            self,
            width=self.canvas_width,
            height=self.canvas_height,
            bg="#09090b",
            highlightthickness=1,
            highlightbackground="#27272a",
        )
        self.canvas.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="nsew")

        # Info bar container for selected key details and Deselect All button
        self.info_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.info_bar.grid(row=1, column=0, padx=10, pady=(2, 8), sticky="ew")
        self.info_bar.grid_columnconfigure(0, weight=1)

        self.selection_label = ctk.CTkLabel(
            self.info_bar,
            text="Click a key on the keyboard layout to inspect identity",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.selection_label.grid(row=0, column=0, sticky="w")

        self.deselect_btn = ctk.CTkButton(
            self.info_bar,
            text="Deselect All",
            width=90,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._on_deselect_all,
        )
        self.deselect_btn.grid(row=0, column=1, sticky="e")

        # Track drawn items for hit testing
        self._key_boxes: Dict[KeyDefinition, Tuple[float, float, float, float]] = {}

        # Drag selection state
        self._drag_start_x: float = 0.0
        self._drag_start_y: float = 0.0
        self._is_dragging: bool = False
        self._drag_shift: bool = False
        self.DRAG_THRESHOLD: float = 5.0

        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.render_layout()

    def render_layout(self) -> None:
        """Render all 84 physical keys on the canvas with Per-Key RGB support."""
        self.canvas.delete("all")
        self._key_boxes.clear()

        matrix = self.controller.get_active_rgb_matrix()
        show_per_key = (
            self.controller.active_tab == "per_key_rgb"
            or (
                self.controller.working_profile
                and self.controller.working_profile.rgb_global
                and self.controller.working_profile.rgb_global.effect == EFFECT_CUSTOM
            )
        )

        for k in TYPE84_LAYOUT:
            x1 = self.CANVAS_MARGIN + k.x * self.U_SIZE + self.KEY_PADDING
            y1 = self.CANVAS_MARGIN + k.y * self.U_SIZE + self.KEY_PADDING
            x2 = x1 + k.width * self.U_SIZE - 2 * self.KEY_PADDING
            y2 = y1 + k.height * self.U_SIZE - 2 * self.KEY_PADDING

            self._key_boxes[k] = (x1, y1, x2, y2)

            is_selected = (
                k.led_slot in self.controller.selected_led_slots
                or k.switch_slot == self.controller.selected_key_slot
            )

            # Determine key fill and text colors
            key_rgb: Optional[Tuple[int, int, int]] = None
            if show_per_key and matrix is not None:
                key_rgb = matrix.get_led(k.led_slot)

            if key_rgb is not None and key_rgb != (0, 0, 0):
                r, g, b = key_rgb
                fill_color = f"#{r:02X}{g:02X}{b:02X}"
                # Perceptual luminance contrast formula
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                text_color = "#000000" if lum > 130 else "#ffffff"
            else:
                fill_color = "#1e293b" if is_selected else "#27272a"
                text_color = "#38bdf8" if is_selected else "#f4f4f5"

            is_remapped = (
                self.controller.active_tab == "remap"
                and self.controller.is_key_remapped(k.switch_slot)
            )
            is_hall_modified = (
                self.controller.active_tab == "hall"
                and self.controller.is_key_hall_modified(k.switch_slot)
            )
            is_dks = (
                self.controller.active_tab in ("dks", "macros")
                and self.controller.is_key_dks(k.switch_slot)
            )
            is_dks_modified = (
                self.controller.active_tab in ("dks", "macros")
                and self.controller.is_key_dks_modified(k.switch_slot)
            )

            if is_selected:
                outline_color = "#38bdf8"
                outline_width = 3
            elif is_remapped:
                outline_color = "#f59e0b"
                outline_width = 2
            elif is_hall_modified:
                outline_color = "#c084fc"
                outline_width = 2
            elif is_dks or is_dks_modified:
                outline_color = "#06b6d4"
                outline_width = 2
            else:
                outline_color = "#3f3f46"
                outline_width = 1

            # Draw key rectangle
            self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=fill_color,
                outline=outline_color,
                width=outline_width,
                tags=("key", f"led_{k.led_slot}", f"switch_{k.switch_slot}"),
            )

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0

            # Draw top-right text badge for remapped key
            if is_remapped:
                info = self.controller.get_key_remap_info(k.switch_slot)
                if info and info.is_modified:
                    if info.is_unbound:
                        badge_txt = "OFF"
                        badge_bg = "#450a0a"
                        badge_border = "#ef4444"
                        badge_fg = "#fecaca"
                    else:
                        badge_txt = _format_remap_badge(info.current_name)
                        badge_bg = "#78350f"
                        badge_border = "#f59e0b"
                        badge_fg = "#fef3c7"

                    bw = max(18, int(len(badge_txt) * 5.4 + 6))
                    bx2 = x2 - 2
                    bx1 = bx2 - bw
                    by1 = y1 + 2
                    by2 = by1 + 12

                    self.canvas.create_rectangle(
                        bx1, by1, bx2, by2,
                        fill=badge_bg,
                        outline=badge_border,
                        width=1,
                        tags=("remap_badge", f"switch_{k.switch_slot}"),
                    )
                    self.canvas.create_text(
                        (bx1 + bx2) / 2.0,
                        (by1 + by2) / 2.0,
                        text=badge_txt,
                        fill=badge_fg,
                        font=("Segoe UI", 7, "bold"),
                        tags=("remap_badge_txt", f"switch_{k.switch_slot}"),
                    )
                    cy += 2.5
            elif is_hall_modified:
                h_info = self.controller.get_hall_info(k.switch_slot)
                if h_info and h_info.is_modified:
                    if h_info.is_rt_enabled:
                        badge_txt = f"RT {h_info.rt_press_mm:.1f}"
                        badge_bg = "#581c87"
                        badge_border = "#c084fc"
                        badge_fg = "#f3e8ff"
                    else:
                        badge_txt = f"{h_info.actuation_mm:.1f}"
                        badge_bg = "#1e1b4b"
                        badge_border = "#818cf8"
                        badge_fg = "#e0e7ff"

                    bw = max(18, int(len(badge_txt) * 5.4 + 6))
                    bx2 = x2 - 2
                    bx1 = bx2 - bw
                    by1 = y1 + 2
                    by2 = by1 + 12

                    self.canvas.create_rectangle(
                        bx1, by1, bx2, by2,
                        fill=badge_bg,
                        outline=badge_border,
                        width=1,
                        tags=("hall_badge", f"switch_{k.switch_slot}"),
                    )
                    self.canvas.create_text(
                        (bx1 + bx2) / 2.0,
                        (by1 + by2) / 2.0,
                        text=badge_txt,
                        fill=badge_fg,
                        font=("Segoe UI", 7, "bold"),
                        tags=("hall_badge_txt", f"switch_{k.switch_slot}"),
                    )
                    cy += 2.5
            elif is_dks or is_dks_modified:
                d_info = self.controller.get_dks_info(k.switch_slot)
                badge_txt = f"DKS #{d_info.dks_slot_index}" if (d_info and d_info.dks_slot_index is not None) else "DKS"
                badge_bg = "#083344"
                badge_border = "#06b6d4"
                badge_fg = "#cffafe"

                bw = max(22, int(len(badge_txt) * 5.4 + 6))
                bx2 = x2 - 2
                bx1 = bx2 - bw
                by1 = y1 + 2
                by2 = by1 + 12

                self.canvas.create_rectangle(
                    bx1, by1, bx2, by2,
                    fill=badge_bg,
                    outline=badge_border,
                    width=1,
                    tags=("dks_badge", f"switch_{k.switch_slot}"),
                )
                self.canvas.create_text(
                    (bx1 + bx2) / 2.0,
                    (by1 + by2) / 2.0,
                    text=badge_txt,
                    fill=badge_fg,
                    font=("Segoe UI", 7, "bold"),
                    tags=("dks_badge_txt", f"switch_{k.switch_slot}"),
                )
                cy += 2.5

            # Draw physical key label (never changed)
            font_size = 8 if len(k.label) > 4 else 9
            self.canvas.create_text(
                cx, cy,
                text=k.label,
                fill=text_color,
                font=("Segoe UI", font_size, "bold"),
                tags=("label", f"led_{k.led_slot}"),
            )

        self._update_selection_info()

    def _on_deselect_all(self) -> None:
        """Deselect all keys."""
        self.controller.deselect_all_leds()
        self.render_layout()

    def _on_canvas_press(self, event: tk.Event) -> None:
        """Record drag start coordinates and modifiers."""
        self._drag_start_x = float(event.x)
        self._drag_start_y = float(event.y)
        self._is_dragging = False
        self._drag_shift = bool(event.state & 0x0001)

    def _on_canvas_motion(self, event: tk.Event) -> None:
        """Draw marquee selection rectangle when dragging beyond threshold."""
        dx = abs(event.x - self._drag_start_x)
        dy = abs(event.y - self._drag_start_y)
        if dx >= self.DRAG_THRESHOLD or dy >= self.DRAG_THRESHOLD:
            self._is_dragging = True
            self.canvas.delete("selection_marquee")
            self.canvas.create_rectangle(
                self._drag_start_x,
                self._drag_start_y,
                event.x,
                event.y,
                dash=(4, 3),
                outline="#38bdf8",
                width=1,
                tags="selection_marquee",
            )

    def _on_canvas_release(self, event: tk.Event) -> None:
        """Complete drag selection or handle single-key click."""
        self.canvas.delete("selection_marquee")
        is_shift = self._drag_shift or bool(event.state & 0x0001)

        if self._is_dragging:
            rx1 = min(self._drag_start_x, float(event.x))
            ry1 = min(self._drag_start_y, float(event.y))
            rx2 = max(self._drag_start_x, float(event.x))
            ry2 = max(self._drag_start_y, float(event.y))

            intersected_slots: set[int] = set()
            for k_def, (kx1, ky1, kx2, ky2) in self._key_boxes.items():
                if not (rx2 < kx1 or rx1 > kx2 or ry2 < ky1 or ry1 > ky2):
                    intersected_slots.add(k_def.led_slot)

            if intersected_slots:
                if is_shift:
                    self.controller.add_leds_to_selection(intersected_slots)
                else:
                    self.controller.set_led_selection(intersected_slots)
        else:
            cx = float(event.x)
            cy = float(event.y)
            clicked_key: Optional[KeyDefinition] = None
            for k_def, (kx1, ky1, kx2, ky2) in self._key_boxes.items():
                if kx1 <= cx <= kx2 and ky1 <= cy <= ky2:
                    clicked_key = k_def
                    break

            if clicked_key is not None:
                if is_shift:
                    self.controller.select_led(clicked_key.led_slot, multi=True)
                else:
                    self.controller.select_led(clicked_key.led_slot, multi=False, toggle=False)
            # When clicking in empty space/margin: do not deselect!

        self._is_dragging = False
        self.render_layout()

    def _update_selection_info(self) -> None:
        """Update information label and Deselect button for the currently selected key(s)."""
        sel_leds = self.controller.selected_led_slots
        matrix = self.controller.get_active_rgb_matrix()

        self.deselect_btn.configure(state="normal" if sel_leds else "disabled")

        if len(sel_leds) == 1:
            slot = next(iter(sel_leds))
            k = KEY_BY_LED_SLOT.get(slot)
            if k:
                if self.controller.active_tab == "remap":
                    info = self.controller.get_key_remap_info(k.switch_slot)
                    cur_binding = info.current_name if info else "Default"
                    self.selection_label.configure(
                        text=f"Selected Key: '{k.label}' ({k.key_id})  |  Switch: {k.switch_slot} (0x{k.switch_slot:02X})  |  Remap Slot: {k.remap_slot} (0x{k.remap_slot:02X})  |  Binding: {cur_binding}",
                        text_color="#f4f4f5",
                    )
                elif self.controller.active_tab == "hall":
                    h_info = self.controller.get_hall_info(k.switch_slot)
                    if h_info:
                        rt_str = f"RT: {h_info.rt_press_mm:.2f}/{h_info.rt_release_mm:.2f} mm" if h_info.is_rt_enabled else "RT: OFF"
                        mod_str = " | [Modified]" if h_info.is_modified else ""
                        self.selection_label.configure(
                            text=f"Selected Key: '{k.label}' ({k.key_id})  |  Switch: {k.switch_slot}  |  Actuation: {h_info.actuation_mm:.2f} mm  |  {rt_str}{mod_str}",
                            text_color="#f4f4f5",
                        )
                    else:
                        self.selection_label.configure(
                            text=f"Selected Key: '{k.label}' ({k.key_id})  |  Switch: {k.switch_slot}",
                            text_color="#f4f4f5",
                        )
                elif self.controller.active_tab in ("dks", "macros"):
                    d_info = self.controller.get_dks_info(k.switch_slot)
                    if d_info and d_info.is_active:
                        act_names = [HID_USAGE_NAMES.get(a, f"Key_{a}") for a in d_info.actions if a != 0]
                        act_disp = ", ".join(act_names) if act_names else "None"
                        mod_str = " | [Modified]" if d_info.is_modified else ""
                        self.selection_label.configure(
                            text=f"Selected Key: '{k.label}' ({k.key_id})  |  DKS Slot: #{d_info.dks_slot_index}  |  Actions: [{act_disp}]  |  Down: {d_info.make_value_1_mm:.1f}/{d_info.make_value_2_mm:.1f} mm{mod_str}",
                            text_color="#06b6d4",
                        )
                    else:
                        self.selection_label.configure(
                            text=f"Selected Key: '{k.label}' ({k.key_id})  |  DKS: Inactive  |  Configure DKS in panel below",
                            text_color="#f4f4f5",
                        )
                else:
                    color = matrix.get_led(slot) if matrix else (0, 0, 0)
                    hex_c = f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"
                    self.selection_label.configure(
                        text=f"Selected Key: '{k.label}' ({k.key_id})  |  LED Slot: {k.led_slot} (0x{k.led_slot:02X})  |  Switch Slot: {k.switch_slot} (0x{k.switch_slot:02X})  |  Color: {hex_c}",
                        text_color="#f4f4f5",
                    )
            else:
                self.selection_label.configure(
                    text=f"Selected LED Slot: {slot}",
                    text_color="#f4f4f5",
                )
        elif len(sel_leds) > 1:
            if self.controller.active_tab == "hall":
                m_info = self.controller.get_multi_hall_info()
                act_str = f"{m_info.actuation_mm:.2f} mm" if not m_info.has_mixed_actuation else "Mixed"
                rt_str = "Mixed" if m_info.has_mixed_rt else ("ON" if m_info.is_rt_enabled else "OFF")
                mod_str = " | [Modified]" if m_info.is_any_modified else ""
                disp_labels = ", ".join(m_info.key_labels[:8])
                more_str = f" ... (+{m_info.count - 8} more)" if m_info.count > 8 else ""
                self.selection_label.configure(
                    text=f"Selected: {m_info.count} keys ({disp_labels}{more_str})  |  Actuation: {act_str}  |  RT: {rt_str}{mod_str}  |  Click 'Apply to Selected'",
                    text_color="#c084fc",
                )
            else:
                labels = [
                    KEY_BY_LED_SLOT[s].label
                    for s in sorted(sel_leds)
                    if s in KEY_BY_LED_SLOT
                ]
                count = len(sel_leds)
                disp_labels = ", ".join(labels[:8])
                more_str = f" ... (+{count - 8} more)" if count > 8 else ""
                self.selection_label.configure(
                    text=f"Selected: {count} keys ({disp_labels}{more_str})  |  Click color or 'Apply to Selected'",
                    text_color="#38bdf8",
                )
        else:
            if self.controller.active_tab == "remap":
                self.selection_label.configure(
                    text="Click key on keyboard canvas to inspect and edit Remap Layer 1 binding",
                    text_color="#a1a1aa",
                )
            elif self.controller.active_tab == "hall":
                self.selection_label.configure(
                    text="Click key to select  |  Drag box to multi-select  |  Shift to add  |  Configure Hall / RT sliders below",
                    text_color="#a1a1aa",
                )
            elif self.controller.active_tab in ("dks", "macros"):
                self.selection_label.configure(
                    text="Click key on keyboard canvas to inspect and configure Dynamic Keystroke (DKS) below",
                    text_color="#a1a1aa",
                )
            else:
                self.selection_label.configure(
                    text="Click key to select  |  Drag box to multi-select  |  Shift to add  |  Paint in Per-Key RGB tab",
                    text_color="#a1a1aa",
                )

    def update_display(self) -> None:
        """Re-render layout reflecting new selection or state."""
        self.render_layout()
