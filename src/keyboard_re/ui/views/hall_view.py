"""
Hall Effect / Rapid Trigger View for IO by Red Square Type 84 Configurator.

Provides interactive controls for:
- Inspecting physical key switch analog actuation thresholds and RT sensitivities.
- Fine-grained slider adjustments with 0.01 mm step precision.
- Rapid Trigger enabled/disabled derived strictly from press/release sensitivities (0.00 mm = OFF).
- Mixed-state handling when multiple keys are selected with differing values.
- Bulk application to selected physical keys (WASD, Arrows, Numbers, All).
- Resetting individual keys or all 84 physical keys back to baseline device state.
- Strict hardware safety: 0.10..4.00 mm boundary clamping, zero mutation of unknown flags (+6..+7).
"""

from __future__ import annotations

from typing import Optional, Set
import customtkinter as ctk

from keyboard_re.ui.controller import AppController, KeyHallInfo, MultiKeyHallInfo
from keyboard_re.ui.layout_data import KEY_BY_SWITCH_SLOT, PHYSICAL_84_SWITCH_SLOTS


class HallView(ctk.CTkFrame):
    """Configuration pane for Hall Effect switch actuation and Rapid Trigger."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.controller = controller

        # Local UI cache for RT sensitivities when toggling ON/OFF
        self._cached_rt_press: float = 0.20
        self._cached_rt_release: float = 0.20

        # UI state tracking
        self._is_updating_ui: bool = False

        self.grid_columnconfigure(0, weight=1, uniform="hall_cols")
        self.grid_columnconfigure(1, weight=1, uniform="hall_cols")

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # ---------------------------------------------------------------------
        # Left Column: Selection & Actuation
        # ---------------------------------------------------------------------
        left_col = ctk.CTkFrame(self, fg_color=("#f4f4f5", "#18181b"), corner_radius=8)
        left_col.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="nsew")
        left_col.grid_columnconfigure(0, weight=1)

        # Header
        self.header_title = ctk.CTkLabel(
            left_col,
            text="Selected Keys",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.header_title.grid(row=0, column=0, padx=15, pady=(15, 2), sticky="w")

        self.header_detail = ctk.CTkLabel(
            left_col,
            text="Click a key on the layout to edit parameters",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.header_detail.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="w")

        # Quick Selection Presets Frame
        presets_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        presets_frame.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="ew")

        preset_btn_configs = [
            ("WASD", lambda: self._select_group("wasd")),
            ("Arrows", lambda: self._select_group("arrows")),
            ("Numbers", lambda: self._select_group("numbers")),
            ("All Keys", self.controller.select_all_leds),
            ("Deselect", self.controller.deselect_all_leds),
        ]
        for i, (label, cmd) in enumerate(preset_btn_configs):
            btn = ctk.CTkButton(
                presets_frame,
                text=label,
                width=65,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color="#27272a",
                hover_color="#3f3f46",
                command=cmd,
            )
            btn.grid(row=0, column=i, padx=(0, 6) if i < len(preset_btn_configs) - 1 else 0)

        # Separator
        sep1 = ctk.CTkFrame(left_col, height=1, fg_color="#27272a")
        sep1.grid(row=3, column=0, padx=15, pady=(0, 15), sticky="ew")

        # Actuation Point Section
        act_header_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        act_header_frame.grid(row=4, column=0, padx=15, pady=(0, 4), sticky="ew")
        act_header_frame.grid_columnconfigure(0, weight=1)

        self.actuation_title = ctk.CTkLabel(
            act_header_frame,
            text="Actuation Point (0.10 .. 4.00 mm)",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        self.actuation_title.grid(row=0, column=0, sticky="w")

        self.actuation_val_label = ctk.CTkLabel(
            act_header_frame,
            text="1.40 mm",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
            anchor="e",
        )
        self.actuation_val_label.grid(row=0, column=1, sticky="e")

        self.actuation_slider = ctk.CTkSlider(
            left_col,
            from_=0.10,
            to=4.00,
            number_of_steps=390,  # 0.01 mm step
            command=self._on_actuation_slider_change,
        )
        self.actuation_slider.grid(row=5, column=0, padx=15, pady=(0, 8), sticky="ew")

        # Actuation Quick Presets
        act_presets_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        act_presets_frame.grid(row=6, column=0, padx=15, pady=(0, 15), sticky="ew")
        for i, val in enumerate([0.50, 1.00, 1.40, 2.00, 3.50]):
            btn = ctk.CTkButton(
                act_presets_frame,
                text=f"{val:.2f}",
                width=55,
                height=24,
                font=ctk.CTkFont(size=11),
                fg_color="#27272a",
                hover_color="#3f3f46",
                command=lambda v=val: self._set_actuation_quick(v),
            )
            btn.grid(row=0, column=i, padx=(0, 6) if i < 4 else 0)

        # ---------------------------------------------------------------------
        # Right Column: Rapid Trigger & Action Buttons
        # ---------------------------------------------------------------------
        right_col = ctk.CTkFrame(self, fg_color=("#f4f4f5", "#18181b"), corner_radius=8)
        right_col.grid(row=0, column=1, padx=(5, 10), pady=10, sticky="nsew")
        right_col.grid_columnconfigure(0, weight=1)

        # Rapid Trigger Header & Toggle
        rt_header_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        rt_header_frame.grid(row=0, column=0, padx=15, pady=(15, 8), sticky="ew")
        rt_header_frame.grid_columnconfigure(0, weight=1)

        self.rt_title = ctk.CTkLabel(
            rt_header_frame,
            text="Rapid Trigger",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.rt_title.grid(row=0, column=0, sticky="w")

        self.rt_switch = ctk.CTkSwitch(
            rt_header_frame,
            text="Enable RT",
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color="#0284c7",
            command=self._on_rt_toggle_change,
        )
        self.rt_switch.grid(row=0, column=1, sticky="e")

        # RT Press Sensitivity
        press_header_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        press_header_frame.grid(row=1, column=0, padx=15, pady=(5, 4), sticky="ew")
        press_header_frame.grid_columnconfigure(0, weight=1)

        self.press_title = ctk.CTkLabel(
            press_header_frame,
            text="Press Sensitivity (0.10 .. 4.00 mm)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.press_title.grid(row=0, column=0, sticky="w")

        self.press_val_label = ctk.CTkLabel(
            press_header_frame,
            text="0.20 mm",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#c084fc",
            anchor="e",
        )
        self.press_val_label.grid(row=0, column=1, sticky="e")

        self.press_slider = ctk.CTkSlider(
            right_col,
            from_=0.10,
            to=4.00,
            number_of_steps=390,
            command=self._on_press_slider_change,
        )
        self.press_slider.grid(row=2, column=0, padx=15, pady=(0, 10), sticky="ew")

        # RT Release Sensitivity
        rel_header_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        rel_header_frame.grid(row=3, column=0, padx=15, pady=(5, 4), sticky="ew")
        rel_header_frame.grid_columnconfigure(0, weight=1)

        self.rel_title = ctk.CTkLabel(
            rel_header_frame,
            text="Release Sensitivity (0.10 .. 4.00 mm)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.rel_title.grid(row=0, column=0, sticky="w")

        self.rel_val_label = ctk.CTkLabel(
            rel_header_frame,
            text="0.20 mm",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#c084fc",
            anchor="e",
        )
        self.rel_val_label.grid(row=0, column=1, sticky="e")

        self.rel_slider = ctk.CTkSlider(
            right_col,
            from_=0.10,
            to=4.00,
            number_of_steps=390,
            command=self._on_release_slider_change,
        )
        self.rel_slider.grid(row=4, column=0, padx=15, pady=(0, 15), sticky="ew")

        # Action Buttons Section
        actions_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        actions_frame.grid(row=5, column=0, padx=15, pady=(10, 15), sticky="ew")
        actions_frame.grid_columnconfigure(0, weight=1)
        actions_frame.grid_columnconfigure(1, weight=1)
        actions_frame.grid_columnconfigure(2, weight=1)

        self.apply_btn = ctk.CTkButton(
            actions_frame,
            text="Apply to Selected",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_apply_clicked,
        )
        self.apply_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.reset_sel_btn = ctk.CTkButton(
            actions_frame,
            text="Reset Selected",
            font=ctk.CTkFont(size=12),
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_reset_selected_clicked,
        )
        self.reset_sel_btn.grid(row=0, column=1, padx=(2, 2), sticky="ew")

        self.reset_all_btn = ctk.CTkButton(
            actions_frame,
            text="Reset All",
            font=ctk.CTkFont(size=12),
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_reset_all_clicked,
        )
        self.reset_all_btn.grid(row=0, column=2, padx=(4, 0), sticky="ew")

    # -------------------------------------------------------------------------
    # Preset & Quick Selection Helpers
    # -------------------------------------------------------------------------

    def _select_group(self, group_name: str) -> None:
        self.controller.select_led_group(group_name)

    def _set_actuation_quick(self, value_mm: float) -> None:
        self.actuation_slider.set(value_mm)
        self.actuation_val_label.configure(text=f"{value_mm:.2f} mm")

    # -------------------------------------------------------------------------
    # Slider & Control Event Handlers
    # -------------------------------------------------------------------------

    def _on_actuation_slider_change(self, value: float) -> None:
        if self._is_updating_ui:
            return
        val_clamped = round(float(value), 2)
        self.actuation_val_label.configure(text=f"{val_clamped:.2f} mm")

    def _on_press_slider_change(self, value: float) -> None:
        if self._is_updating_ui:
            return
        val_clamped = round(float(value), 2)
        self._cached_rt_press = val_clamped
        self.press_val_label.configure(text=f"{val_clamped:.2f} mm")

    def _on_release_slider_change(self, value: float) -> None:
        if self._is_updating_ui:
            return
        val_clamped = round(float(value), 2)
        self._cached_rt_release = val_clamped
        self.rel_val_label.configure(text=f"{val_clamped:.2f} mm")

    def _on_rt_toggle_change(self) -> None:
        if self._is_updating_ui:
            return
        is_on = bool(self.rt_switch.get())
        self._update_rt_slider_states(is_on)

    def _update_rt_slider_states(self, is_enabled: bool) -> None:
        """Enable or disable RT sliders based on toggle state."""
        state_str = "normal" if is_enabled else "disabled"
        self.press_slider.configure(state=state_str)
        self.rel_slider.configure(state=state_str)

        if is_enabled:
            # Restore cached sensitivities
            press = max(0.10, self._cached_rt_press)
            rel = max(0.10, self._cached_rt_release)
            self.press_slider.set(press)
            self.rel_slider.set(rel)
            self.press_val_label.configure(text=f"{press:.2f} mm", text_color="#c084fc")
            self.rel_val_label.configure(text=f"{rel:.2f} mm", text_color="#c084fc")
        else:
            self.press_val_label.configure(text="OFF (0.00 mm)", text_color="#71717a")
            self.rel_val_label.configure(text="OFF (0.00 mm)", text_color="#71717a")

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        """Apply currently configured slider values to all selected physical switches."""
        slots = self.controller.selected_switch_slots
        if not slots:
            return

        act_mm = round(float(self.actuation_slider.get()), 2)
        rt_is_on = bool(self.rt_switch.get())

        if rt_is_on:
            press_mm = round(float(self.press_slider.get()), 2)
            rel_mm = round(float(self.rel_slider.get()), 2)
        else:
            press_mm = 0.00
            rel_mm = 0.00

        self.controller.set_hall_parameters(
            slots,
            actuation_mm=act_mm,
            rt_press_mm=press_mm,
            rt_release_mm=rel_mm,
        )

    def _on_reset_selected_clicked(self) -> None:
        slots = self.controller.selected_switch_slots
        if slots:
            self.controller.reset_hall_keys(slots)

    def _on_reset_all_clicked(self) -> None:
        self.controller.reset_all_hall()

    # -------------------------------------------------------------------------
    # View State Synchronization
    # -------------------------------------------------------------------------

    def update_display(self) -> None:
        """Refresh controls based on current controller selection and state."""
        self._is_updating_ui = True
        try:
            slots = self.controller.selected_switch_slots
            count = len(slots)

            if count == 0:
                self.header_title.configure(text="No Keys Selected")
                self.header_detail.configure(
                    text="Click a key on the layout or select a group above to begin",
                    text_color="#a1a1aa",
                )
                self.actuation_val_label.configure(text="--", text_color="#71717a")
                self.press_val_label.configure(text="--", text_color="#71717a")
                self.rel_val_label.configure(text="--", text_color="#71717a")
                self.actuation_slider.configure(state="disabled")
                self.rt_switch.configure(state="disabled")
                self.press_slider.configure(state="disabled")
                self.rel_slider.configure(state="disabled")
                self.apply_btn.configure(state="disabled")
                self.reset_sel_btn.configure(state="disabled")
                return

            # Enable general controls
            self.actuation_slider.configure(state="normal")
            self.rt_switch.configure(state="normal")
            self.apply_btn.configure(state="normal")
            self.reset_sel_btn.configure(state="normal")

            if count == 1:
                slot = next(iter(slots))
                k_def = KEY_BY_SWITCH_SLOT.get(slot)
                info = self.controller.get_hall_info(slot)

                lbl = k_def.label if k_def else f"Switch {slot}"
                mod_txt = " [Modified]" if (info and info.is_modified) else ""
                self.header_title.configure(text=f"Selected Key: '{lbl}' (Switch {slot}){mod_txt}")

                if info:
                    rt_status_txt = (
                        f"RT: {info.rt_press_mm:.2f} / {info.rt_release_mm:.2f} mm"
                        if info.is_rt_enabled
                        else "RT: OFF"
                    )
                    self.header_detail.configure(
                        text=f"Default: Actuation {info.default_actuation_mm:.2f} mm  |  {rt_status_txt}",
                        text_color="#f4f4f5",
                    )
                    self.actuation_slider.set(info.actuation_mm)
                    self.actuation_val_label.configure(
                        text=f"{info.actuation_mm:.2f} mm",
                        text_color="#38bdf8",
                    )

                    if info.is_rt_enabled:
                        self.rt_switch.select()
                        self._cached_rt_press = info.rt_press_mm
                        self._cached_rt_release = info.rt_release_mm
                        self._update_rt_slider_states(True)
                        self.press_slider.set(info.rt_press_mm)
                        self.rel_slider.set(info.rt_release_mm)
                    else:
                        self.rt_switch.deselect()
                        self._update_rt_slider_states(False)
            else:
                m_info = self.controller.get_multi_hall_info(slots)
                disp_keys = ", ".join(m_info.key_labels[:8])
                more_txt = f" ... (+{count - 8} more)" if count > 8 else ""
                mod_txt = " [Modified]" if m_info.is_any_modified else ""
                self.header_title.configure(text=f"Selected: {count} keys ({disp_keys}{more_txt}){mod_txt}")

                # Actuation display
                if not m_info.has_mixed_actuation and m_info.actuation_mm is not None:
                    self.actuation_slider.set(m_info.actuation_mm)
                    self.actuation_val_label.configure(
                        text=f"{m_info.actuation_mm:.2f} mm",
                        text_color="#38bdf8",
                    )
                else:
                    self.actuation_val_label.configure(text="Mixed", text_color="#f59e0b")

                # RT display
                if not m_info.has_mixed_rt and m_info.is_rt_enabled is not None:
                    if m_info.is_rt_enabled:
                        self.rt_switch.select()
                        if m_info.rt_press_mm is not None:
                            self._cached_rt_press = m_info.rt_press_mm
                            self.press_slider.set(m_info.rt_press_mm)
                        if m_info.rt_release_mm is not None:
                            self._cached_rt_release = m_info.rt_release_mm
                            self.rel_slider.set(m_info.rt_release_mm)
                        self._update_rt_slider_states(True)
                    else:
                        self.rt_switch.deselect()
                        self._update_rt_slider_states(False)
                else:
                    # Mixed RT state: show current toggle switch position
                    if bool(self.rt_switch.get()):
                        self._update_rt_slider_states(True)
                        if m_info.rt_press_mm is None:
                            self.press_val_label.configure(text="Mixed", text_color="#f59e0b")
                        if m_info.rt_release_mm is None:
                            self.rel_val_label.configure(text="Mixed", text_color="#f59e0b")
                    else:
                        self._update_rt_slider_states(False)

                self.header_detail.configure(
                    text="Values apply uniformly to all selected keys on 'Apply to Selected'",
                    text_color="#a1a1aa",
                )
        finally:
            self._is_updating_ui = False
