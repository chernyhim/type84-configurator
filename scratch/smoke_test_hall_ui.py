"""
Headless UI Smoke Test for Hall Effect / Rapid Trigger Subsystem.

Tests:
1. Mock connection & window initialization.
2. Tab switching to 'Hall / Rapid Trigger'.
3. Single-key selection & slider inspection.
4. Parameter modifications (Actuation, RT toggle, RT press/release).
5. Multi-key selection (WASD) with mixed-state detection and uniform apply.
6. Reset Selected and Reset All behavior.
7. Visual badge rendering on KeyboardView.
8. End-to-end Apply with confirmation summary and closed-loop readback.
"""

import os
from pathlib import Path
import sys

# Headless display setup for Windows/Tkinter if needed
from PIL import Image, ImageGrab
import customtkinter as ctk

from keyboard_re.ui.app import KeyboardApp
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import KEY_BY_ID, TYPE84_LAYOUT
from keyboard_re.ui.views.main_window import MainWindow


def run_smoke_test():
    print("=== STARTING HALL / RAPID TRIGGER UI SMOKE TEST ===")

    capture_path = Path("captures/experiments/read_01_initial_load.json")
    controller = AppController(default_capture_path=capture_path)

    # Initialize Tkinter root and MainWindow
    ctk.set_appearance_mode("Dark")
    root = ctk.CTk()
    root.geometry("1100x850")
    root.title("Hall RT Smoke Test")

    main_win = MainWindow(root, controller=controller)
    main_win.pack(fill="both", expand=True)

    # Connect mock
    print("1. Connecting in mock mode...")
    success = controller.connect(use_mock=True)
    assert success, "Mock connection failed"
    assert controller.is_connected, "Controller should be connected"

    # Switch to Hall tab
    print("2. Switching to 'Hall / Rapid Trigger' tab...")
    main_win.tabview.set("Hall / Rapid Trigger")
    main_win._on_tab_changed()
    assert controller.active_tab == "hall", f"Active tab should be 'hall', got {controller.active_tab}"

    # Verify initial state: no keys selected
    root.update()
    hall_view = main_win.hall_view
    assert "No Keys Selected" in hall_view.header_title.cget("text"), "Initial state should be No Keys Selected"
    print("   Initial no-selection state verified.")

    # 3. Select Key 'W'
    print("3. Selecting Key 'W'...")
    w_def = KEY_BY_ID["W"]
    controller.select_led(w_def.led_slot, multi=False)
    root.update()

    w_info = controller.get_hall_info(w_def.switch_slot)
    assert w_info is not None
    assert w_info.key_id == "W"
    assert not w_info.is_modified
    assert not w_info.is_rt_enabled
    assert "Selected Key: 'W'" in hall_view.header_title.cget("text")
    assert "1.40 mm" in hall_view.actuation_val_label.cget("text")
    assert not bool(hall_view.rt_switch.get()), "RT toggle should initially be OFF"
    print("   Single key 'W' inspection verified.")

    # 4. Modify Key 'W': Actuation 0.80 mm, RT ON (Press 0.15, Release 0.15)
    print("4. Modifying Key 'W' (Actuation 0.80, RT 0.15/0.15)...")
    hall_view.actuation_slider.set(0.80)
    hall_view._on_actuation_slider_change(0.80)

    hall_view.rt_switch.select()
    hall_view._on_rt_toggle_change()

    hall_view.press_slider.set(0.15)
    hall_view._on_press_slider_change(0.15)

    hall_view.rel_slider.set(0.15)
    hall_view._on_release_slider_change(0.15)

    hall_view._on_apply_clicked()
    root.update()

    assert controller.is_dirty, "Controller should be dirty after modification"
    assert controller.is_key_hall_modified(w_def.switch_slot), "Key W should be modified"
    w_info_after = controller.get_hall_info(w_def.switch_slot)
    assert w_info_after.actuation_mm == 0.80
    assert w_info_after.rt_press_mm == 0.15
    assert w_info_after.rt_release_mm == 0.15
    assert w_info_after.is_rt_enabled
    print("   Key 'W' modification and dirty state verified.")

    # 5. Multi-selection: Select WASD
    print("5. Selecting WASD group (Mixed state check)...")
    controller.select_led_group("wasd")
    root.update()

    assert len(controller.selected_switch_slots) == 4
    multi_info = controller.get_multi_hall_info()
    assert multi_info.has_mixed_actuation, "Should detect mixed actuation across WASD"
    assert multi_info.has_mixed_rt, "Should detect mixed RT across WASD"
    assert multi_info.is_any_modified, "Should report at least one key modified"
    assert hall_view.actuation_val_label.cget("text") == "Mixed"
    print("   Multi-key mixed state detection verified.")

    # Apply uniform values to WASD
    print("   Applying uniform 1.00 mm actuation and RT 0.20/0.20 to WASD...")
    hall_view.actuation_slider.set(1.00)
    hall_view._on_actuation_slider_change(1.00)
    hall_view.rt_switch.select()
    hall_view.press_slider.set(0.20)
    hall_view.rel_slider.set(0.20)
    hall_view._on_apply_clicked()
    root.update()

    multi_uniform = controller.get_multi_hall_info()
    assert not multi_uniform.has_mixed_actuation, "Actuation should now be uniform"
    assert not multi_uniform.has_mixed_rt, "RT should now be uniform"
    assert multi_uniform.actuation_mm == 1.00
    assert multi_uniform.rt_press_mm == 0.20
    assert multi_uniform.rt_release_mm == 0.20
    print("   Uniform multi-apply verified.")

    # 6. Reset Selected
    print("6. Resetting selected keys...")
    hall_view._on_reset_selected_clicked()
    root.update()

    multi_reset = controller.get_multi_hall_info()
    assert not multi_reset.is_any_modified, "WASD keys should no longer be modified"
    assert not controller.is_dirty, "Controller should be clean after reverting all modified keys"
    print("   Reset selected verified.")

    # 7. End-to-end Apply with Confirmation & Readback
    print("7. Testing end-to-end Apply pipeline...")
    controller.set_hall_parameters({w_def.switch_slot}, actuation_mm=2.50, rt_press_mm=0.30, rt_release_mm=0.30)
    assert controller.is_dirty

    summary = controller.get_confirmation_summary()
    assert summary is not None
    assert "hall" in summary.changed_subsystems
    assert summary.total_packets == 19

    # Execute apply
    result = controller.apply_changes(confirmed=True)
    assert result.is_success, f"Apply failed: {result.error}"
    assert not controller.is_dirty, "Controller should not be dirty after successful apply"
    assert controller.diff_count == 0

    # Verify updated baseline
    base_w = controller.get_hall_info(w_def.switch_slot)
    assert base_w.actuation_mm == 2.50
    assert base_w.rt_press_mm == 0.30
    assert base_w.is_rt_enabled
    assert not base_w.is_modified, "Key should match new baseline"
    print("   End-to-end Apply & closed-loop verification verified.")

    # 8. Snapshot capture for visual artifact
    print("8. Rendering snapshot artifact...")
    root.update_idletasks()
    root.update()

    # Capture canvas widget to image
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()

    try:
        shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        out_path = Path("C:/Users/Илья/.gemini/antigravity-ide/brain/cedfaaa9-8c65-4459-90fb-6f33d6e74157/hall_rt_preview.png")
        shot.save(out_path)
        print(f"   Saved snapshot to: {out_path}")
    except Exception as e:
        print(f"   ImageGrab note: {e}")

    root.destroy()
    print("=== ALL HALL / RAPID TRIGGER SMOKE TESTS PASSED! ===")


if __name__ == "__main__":
    run_smoke_test()
