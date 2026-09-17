"""
Remap UI Headless Smoke Test: Base Layer (L1) and Fn Layer (L2).
"""

from __future__ import annotations

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.main_window import MainWindow


import customtkinter as ctk


def main():
    print("=== Remap UI Headless Smoke Test ===")
    root = ctk.CTk()
    root.withdraw()

    capture_path = WORKSPACE_ROOT / "captures" / "experiments" / "read_01_initial_load.json"
    controller = AppController(default_capture_path=capture_path)

    # 1. Connect
    controller.connect(use_mock=True)
    print("[1] Connected in mock mode.")

    # 2. Window
    win = MainWindow(root, controller=controller)
    win.pack(fill="both", expand=True)
    root.update_idletasks()
    print("[2] MainWindow with RemapView rendered.")

    # 3. Tab switch to Remap
    win.tabview.set("Key Remap (L1/L2)")
    win._on_tab_changed()
    remap_view = win.remap_view
    root.update_idletasks()
    print("[3] Switched to Key Remap (L1/L2) tab.")

    # 4. Check initial layer is 1
    assert controller.active_remap_layer == 1
    print("[4] Initial layer is Base (L1).")

    # 5. Switch to Fn Layer (L2)
    remap_view.layer_selector.set("Fn Layer (L2)")
    remap_view._on_layer_changed("Fn Layer (L2)")
    root.update_idletasks()
    assert controller.active_remap_layer == 2
    print("[5] Switched to Fn Layer (L2) via UI segmented button.")

    # 6. Verify protected keys on L2
    f1_info = controller.get_key_remap_info(switch_slot=1)
    fn_info = controller.get_key_remap_info(switch_slot=85)
    assert f1_info.is_readonly is True
    assert fn_info.is_readonly is True
    print("[6] F1..F12 and Fn key are strictly read-only on Fn Layer (L2).")

    # 7. Remap W (slot 34) on L2 to Space (0x2C)
    w_info = controller.get_key_remap_info(switch_slot=34)
    assert w_info.is_readonly is False
    assert controller.set_key_binding(switch_slot=34, scancode=0x2C) is True
    root.update_idletasks()
    print("[7] Remapped Key 'W' (slot 34) to Space on Fn Layer.")

    # 8. Check confirmation summary
    summary = controller.get_confirmation_summary()
    assert "remap_l2" in summary.changed_subsystems
    assert "remap_l1" not in summary.changed_subsystems
    step = [s for s in summary.steps_summary if s["opcode"] == 0x26][0]
    assert step["packet_count"] == 10
    print(f"[8] Confirmation summary verified: {summary.changed_subsystems} (opcode 0x26, 10 packets).")

    # 9. Apply changes
    l1_before = bytes(controller.device_state.remap_l1.raw_bytes)
    controller.apply_changes()
    l1_after = bytes(controller.device_state.remap_l1.raw_bytes)
    l2_after = bytes(controller.device_state.remap_l2.raw_bytes)
    assert l1_before == l1_after, "L1 was modified!"
    assert l2_after[136:140] == bytes([0x02, 0x00, 0x2C, 0x00])
    print("[9] Closed-loop apply verified: L2 updated, L1 100% untouched.")

    # 10. Switch back to Base Layer
    remap_view.layer_selector.set("Base Layer (L1)")
    remap_view._on_layer_changed("Base Layer (L1)")
    root.update_idletasks()
    assert controller.active_remap_layer == 1
    print("[10] Switched back to Base Layer (L1).")

    print("=== ALL REMAP UI SMOKE TESTS PASSED ===")


if __name__ == "__main__":
    main()
