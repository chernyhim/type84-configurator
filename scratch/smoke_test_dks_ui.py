"""
Headless smoke test for DKS UI integration in mock mode.
"""

from __future__ import annotations

import os
import sys

from keyboard_re.models.dks import DKSEventState
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import KEY_BY_ID
from keyboard_re.ui.views.main_window import MainWindow

import customtkinter as ctk

def main():
    print("=== DKS UI Headless Smoke Test ===")
    root = ctk.CTk()
    root.withdraw()

    controller = AppController()
    ok = controller.connect(use_mock=True)
    assert ok, "Mock connect failed"
    print("[1] Connected in mock mode.")

    win = MainWindow(root, controller=controller)
    win.pack(fill="both", expand=True)
    root.update_idletasks()
    print("[2] MainWindow with DKSView rendered.")

    # Switch to DKS tab
    win.tabview.set("DKS")
    win._on_tab_changed()
    assert controller.active_tab == "dks"
    print("[3] Switched to DKS tab.")

    # Select key 'W'
    w_slot = KEY_BY_ID["W"].switch_slot
    controller.select_key_by_switch(w_slot)
    root.update_idletasks()
    win.dks_view.update_display()
    print(f"[4] Key 'W' (switch {w_slot}) selected.")

    # Enable DKS
    win.dks_view._on_toggle_dks()
    root.update_idletasks()
    assert controller.is_key_dks(w_slot), "DKS was not activated on W"
    info = controller.get_dks_info(w_slot)
    assert info.is_active
    print(f"[5] DKS enabled on W: slot #{info.dks_slot_index}, actions: {info.actions}")

    # Adjust travel points and matrix states
    win.dks_view._on_make1_change(1.8)
    win.dks_view._on_make2_change(3.2)
    win.dks_view._on_cell_click(0, 1)  # toggle action 0 point 1
    win.dks_view._on_apply()
    root.update_idletasks()
    print("[6] Applied adjusted parameters.")

    # Check dirty tracking
    assert controller.is_dirty, "Controller must be dirty"
    summary = controller.get_confirmation_summary()
    assert summary is not None
    print(f"[7] Confirmation summary: {len(summary.changed_subsystems)} subsystem(s) queued: {summary.changed_subsystems}")

    # Closed-loop mock apply
    res = controller.apply_changes(confirmed=True)
    assert res.is_success, f"Apply failed: {res.error}"
    assert not controller.is_dirty, "Controller dirty flag not cleared after apply"
    print("[8] Closed-loop mock apply succeeded.")

    # Verify device state readback
    assert controller.device_state.dks_table is not None
    rec = controller.device_state.dks_table.get_record(info.dks_slot_index)
    assert rec.make_value_1_mm == 1.8
    print("[9] Closed-loop readback verified in device_state.")

    root.destroy()
    print("=== ALL SMOKE TESTS PASSED ===")

if __name__ == "__main__":
    main()
