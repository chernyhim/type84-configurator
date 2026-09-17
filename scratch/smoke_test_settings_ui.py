"""
Headless smoke test for Settings / Game Mode UI integration in mock mode.
"""

from __future__ import annotations

import customtkinter as ctk

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.main_window import MainWindow


def main():
    print("=== Settings UI Headless Smoke Test ===")
    root = ctk.CTk()
    root.withdraw()

    controller = AppController()
    ok = controller.connect(use_mock=True)
    assert ok, "Mock connect failed"
    print("[1] Connected in mock mode.")

    win = MainWindow(root, controller=controller)
    win.pack(fill="both", expand=True)
    root.update_idletasks()
    print("[2] MainWindow with SettingsView rendered.")

    # Switch to Settings tab
    win.tabview.set("Settings")
    win._on_tab_changed()
    assert controller.active_tab == "settings"
    print("[3] Switched to Settings tab.")

    # Check initial values in view
    initial_info = controller.get_game_mode_info()
    assert initial_info is not None
    assert win.settings_view.rate_segmented.get() == "8000 Hz"
    assert win.settings_view.stability_switch.get() == 1
    print(f"[4] Initial values verified: rate={win.settings_view.rate_segmented.get()}, stability={win.settings_view.stability_switch.get()}.")

    # Interact with UI controls
    win.settings_view._on_rate_changed("1000 Hz")
    root.update_idletasks()
    assert controller.get_game_mode_info().report_rate_hz == 1000
    assert controller.is_dirty
    print("[5] Polling rate changed to 1000 Hz via segmented button.")

    win.settings_view.stability_switch.deselect()
    win.settings_view._on_stability_changed()
    root.update_idletasks()
    assert controller.get_game_mode_info().stability_mode == 0
    print("[6] Stability mode disabled via switch.")

    win.settings_view.game_mode_switch.select()
    win.settings_view._on_game_mode_changed()
    root.update_idletasks()
    assert controller.get_game_mode_info().game_mode == 1
    print("[7] Game mode (Win lock) enabled via switch.")

    win.settings_view._on_sleep_slider_changed(15.0)
    root.update_idletasks()
    assert controller.get_game_mode_info().sleep_time == 15
    print("[8] Sleep timeout changed to 15 min via slider.")

    # Verify write plan generation
    summary = controller.get_confirmation_summary()
    assert summary is not None
    assert summary.total_packets == 1
    assert "game_mode" in summary.changed_subsystems
    opcodes = [s["opcode"] for s in summary.steps_summary]
    assert 0x21 in opcodes
    print(f"[9] Write plan contains game_mode (opcode 0x21, {summary.total_packets} packet).")

    # Test Apply in mock mode
    result = controller.apply_changes(confirmed=True)
    assert result.is_success
    assert not controller.is_dirty
    assert controller.device_state.game_mode.report_rate_hz == 1000
    assert controller.device_state.game_mode.stability_mode == 0
    assert controller.device_state.game_mode.game_mode == 1
    assert controller.device_state.game_mode.sleep_time == 15
    print("[10] Applied changes in mock mode: device state updated, dirty flag cleared.")

    root.destroy()
    print("=== All 10 Smoke Test Stages PASSED ===")


if __name__ == "__main__":
    main()
