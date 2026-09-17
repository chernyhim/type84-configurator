"""
Settings / Game Mode View for IO by Red Square Type 84 Configurator.

Provides interactive controls for device-global hardware settings (AA 11 / AA 21):
- Polling Rate (1000 Hz / 4000 Hz / 8000 Hz)
- Stability Mode & Auto Calibration
- Game Mode (Win Lock) & Fn Switch
- Sleep Timeout & Key Debounce Delay
- Top & Bottom Analog Deadzones
- OS System Mode (Windows / Mac)
"""

from __future__ import annotations

from typing import Optional
import customtkinter as ctk

from keyboard_re.protocol.read import GameModeResponse
from keyboard_re.ui.controller import AppController


class SettingsView(ctk.CTkFrame):
    """View panel for configuring Game Mode and hardware performance settings."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller
        self._updating: bool = False

        self.grid_columnconfigure(0, weight=1, uniform="settings_cols")
        self.grid_columnconfigure(1, weight=1, uniform="settings_cols")

        self._build_widgets()
        self.update_display()

    def _build_widgets(self) -> None:
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Device Settings & Performance (AA 11 / AA 21)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        # ---------------------------------------------------------------------
        # Left Column: Performance & Hardware Mode
        # ---------------------------------------------------------------------
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.grid(row=1, column=0, padx=15, pady=5, sticky="nsew")

        # 1. Polling Rate
        ctk.CTkLabel(
            left_frame,
            text="Polling Rate (Report Rate):",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", pady=(5, 2))

        self.rate_segmented = ctk.CTkSegmentedButton(
            left_frame,
            values=["1000 Hz", "4000 Hz", "8000 Hz"],
            command=self._on_rate_changed,
        )
        self.rate_segmented.pack(fill="x", pady=(0, 15))

        # 2. Stability Mode Switch
        self.stability_switch = ctk.CTkSwitch(
            left_frame,
            text="Stability Mode (Hall Sensor Filter)",
            font=ctk.CTkFont(size=12),
            command=self._on_stability_changed,
        )
        self.stability_switch.pack(anchor="w", pady=(0, 10))

        # 3. Auto Calibration Switch
        self.auto_cal_switch = ctk.CTkSwitch(
            left_frame,
            text="Auto Calibration (Magnetic Self-Cal)",
            font=ctk.CTkFont(size=12),
            command=self._on_auto_cal_changed,
        )
        self.auto_cal_switch.pack(anchor="w", pady=(0, 10))

        # 4. OS System Mode
        ctk.CTkLabel(
            left_frame,
            text="Operating System Layout Mode:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", pady=(5, 2))

        self.system_mode_seg = ctk.CTkSegmentedButton(
            left_frame,
            values=["Windows", "Mac"],
            command=self._on_system_mode_changed,
        )
        self.system_mode_seg.pack(fill="x", pady=(0, 15))

        # ---------------------------------------------------------------------
        # Right Column: System & Key Behaviors
        # ---------------------------------------------------------------------
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=1, column=1, padx=15, pady=5, sticky="nsew")

        # 5. Game Mode (Win Key Lock)
        self.game_mode_switch = ctk.CTkSwitch(
            right_frame,
            text="Game Mode (Lock Windows Key)",
            font=ctk.CTkFont(size=12),
            command=self._on_game_mode_changed,
        )
        self.game_mode_switch.pack(anchor="w", pady=(5, 10))

        # 6. Fn Switch
        self.fn_switch = ctk.CTkSwitch(
            right_frame,
            text="Fn Switch (Swap Fn Default Layer)",
            font=ctk.CTkFont(size=12),
            command=self._on_fn_switch_changed,
        )
        self.fn_switch.pack(anchor="w", pady=(0, 15))

        # 7. Sleep Timeout Slider
        self.sleep_label = ctk.CTkLabel(
            right_frame,
            text="Sleep Timeout: 1 min",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.sleep_label.pack(anchor="w", pady=(0, 2))

        self.sleep_slider = ctk.CTkSlider(
            right_frame,
            from_=0,
            to=30,
            number_of_steps=30,
            command=self._on_sleep_slider_changed,
        )
        self.sleep_slider.pack(fill="x", pady=(0, 10))

        # 8. Key Delay Debounce Slider
        self.delay_label = ctk.CTkLabel(
            right_frame,
            text="Debounce Delay: 0 ms",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.delay_label.pack(anchor="w", pady=(0, 2))

        self.delay_slider = ctk.CTkSlider(
            right_frame,
            from_=0,
            to=10,
            number_of_steps=10,
            command=self._on_delay_slider_changed,
        )
        self.delay_slider.pack(fill="x", pady=(0, 10))

        # 9. Deadzones
        self.deadzone_label = ctk.CTkLabel(
            right_frame,
            text="Deadzones (Top / Bottom): 0.00 mm / 0.00 mm",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.deadzone_label.pack(anchor="w", pady=(0, 2))

        deadzone_row = ctk.CTkFrame(right_frame, fg_color="transparent")
        deadzone_row.pack(fill="x", pady=(0, 10))

        self.top_deadzone_slider = ctk.CTkSlider(
            deadzone_row,
            from_=0.0,
            to=0.50,
            number_of_steps=50,
            command=self._on_deadzones_changed,
        )
        self.top_deadzone_slider.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.bottom_deadzone_slider = ctk.CTkSlider(
            deadzone_row,
            from_=0.0,
            to=0.50,
            number_of_steps=50,
            command=self._on_deadzones_changed,
        )
        self.bottom_deadzone_slider.pack(side="right", fill="x", expand=True, padx=(5, 0))

        # ---------------------------------------------------------------------
        # Bottom Row: Actions
        # ---------------------------------------------------------------------
        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.grid(row=2, column=0, columnspan=2, padx=15, pady=(10, 15), sticky="ew")

        self.reset_btn = ctk.CTkButton(
            action_row,
            text="Reset Settings to Device Defaults",
            font=ctk.CTkFont(size=12),
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_reset_clicked,
        )
        self.reset_btn.pack(side="right")

    def update_display(self) -> None:
        """Synchronize UI controls with current working profile / device state."""
        info: Optional[GameModeResponse] = self.controller.get_game_mode_info()
        if not info:
            return

        self._updating = True
        try:
            # Polling Rate
            hz = info.report_rate_hz
            self.rate_segmented.set(f"{hz} Hz")

            # Switches
            if info.stability_mode:
                self.stability_switch.select()
            else:
                self.stability_switch.deselect()

            if info.auto_calibration:
                self.auto_cal_switch.select()
            else:
                self.auto_cal_switch.deselect()

            if info.game_mode:
                self.game_mode_switch.select()
            else:
                self.game_mode_switch.deselect()

            if info.fn_switch:
                self.fn_switch.select()
            else:
                self.fn_switch.deselect()

            # System mode
            self.system_mode_seg.set("Mac" if info.system_mode == 1 else "Windows")

            # Sleep
            self.sleep_slider.set(info.sleep_time)
            self.sleep_label.configure(text=f"Sleep Timeout: {info.sleep_time} min")

            # Key Delay
            self.delay_slider.set(info.key_delay)
            self.delay_label.configure(text=f"Debounce Delay: {info.key_delay} ms")

            # Deadzones
            self.top_deadzone_slider.set(info.top_deadzone)
            self.bottom_deadzone_slider.set(info.bottom_deadzone)
            self.deadzone_label.configure(
                text=f"Deadzones (Top / Bottom): {info.top_deadzone:.2f} mm / {info.bottom_deadzone:.2f} mm"
            )
        finally:
            self._updating = False

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _on_rate_changed(self, value: str) -> None:
        if self._updating:
            return
        hz = int(value.replace(" Hz", "").strip())
        self.controller.set_report_rate_hz(hz)

    def _on_stability_changed(self) -> None:
        if self._updating:
            return
        self.controller.set_stability_mode(bool(self.stability_switch.get()))

    def _on_auto_cal_changed(self) -> None:
        if self._updating:
            return
        self.controller.set_auto_calibration(bool(self.auto_cal_switch.get()))

    def _on_game_mode_changed(self) -> None:
        if self._updating:
            return
        self.controller.set_game_mode(bool(self.game_mode_switch.get()))

    def _on_fn_switch_changed(self) -> None:
        if self._updating:
            return
        self.controller.set_fn_switch(bool(self.fn_switch.get()))

    def _on_system_mode_changed(self, value: str) -> None:
        if self._updating:
            return
        mode = 1 if value == "Mac" else 0
        self.controller.set_system_mode(mode)

    def _on_sleep_slider_changed(self, value: float) -> None:
        val = int(round(value))
        self.sleep_label.configure(text=f"Sleep Timeout: {val} min")
        if not self._updating:
            self.controller.set_sleep_time(val)

    def _on_delay_slider_changed(self, value: float) -> None:
        val = int(round(value))
        self.delay_label.configure(text=f"Debounce Delay: {val} ms")
        if not self._updating:
            self.controller.set_key_delay(val)

    def _on_deadzones_changed(self, _=None) -> None:
        top_val = round(self.top_deadzone_slider.get(), 2)
        bottom_val = round(self.bottom_deadzone_slider.get(), 2)
        self.deadzone_label.configure(
            text=f"Deadzones (Top / Bottom): {top_val:.2f} mm / {bottom_val:.2f} mm"
        )
        if not self._updating:
            self.controller.set_deadzones(top_val, bottom_val)

    def _on_reset_clicked(self) -> None:
        self.controller.reset_game_mode_to_default()
        self.update_display()
