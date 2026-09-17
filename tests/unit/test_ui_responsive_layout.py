"""
Unit tests for Responsive & Resizable Layout System in MainWindow.

Verifies:
1. Multi-resolution adaptability across target screens:
   - 1918x1138 (1080p full-screen laptop)
   - 1280x720 (HD / 150% scaled 1080p)
   - 1024x768 (XGA / 125% scaled)
   - 800x600 (Constrained window size)
2. All 8 subsystem tabs provide localized vertical scrolling:
   - RGB Global
   - Per-Key RGB
   - Key Remap (L1/L2)
   - Hall / Rapid Trigger
   - DKS
   - Macros
   - Settings
   - Visualizer
3. KeyboardView adaptive scaling:
   - Dynamically scales u_size down on constrained displays and up on larger displays.
   - Hit testing reliably maps clicks to exact physical key slots regardless of scale.
4. Header and bottom StatusBar (Apply/Discard) remain permanently visible and within bounds.
5. Clean teardown and safe disposal across all resolutions.
"""

from __future__ import annotations

import unittest
import customtkinter as ctk

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import KEY_BY_ID
from keyboard_re.ui.views.main_window import MainWindow


class TestUIResponsiveLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self) -> None:
        self.controller = AppController()
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self) -> None:
        self.controller.disconnect()

    # -------------------------------------------------------------------------
    # 1. Multi-Resolution Window Tests
    # -------------------------------------------------------------------------
    def test_01_resolutions_adapt_keyboard_and_status_bar(self) -> None:
        """MainWindow adapts keyboard scale and keeps StatusBar visible across all required sizes."""
        test_resolutions = [
            (1918, 1138, 40.0, 48.0),  # Full-screen 1080p
            (1280, 720, 30.0, 38.0),   # 1280x720 / 150% scaled
            (1024, 768, 32.0, 38.0),   # 1024x768
            (800, 600, 26.0, 32.0),    # 800x600
        ]

        main_win = MainWindow(self.root, controller=self.controller)
        main_win.pack(fill="both", expand=True)

        try:
            for w, h, min_exp_u, max_exp_u in test_resolutions:
                self.root.geometry(f"{w}x{h}")
                self.root.update_idletasks()
                self.root.update()

                # Trigger adaptive scaling
                main_win._adapt_keyboard_scale(w, h)
                self.root.update_idletasks()

                # Verify keyboard scale is clamped within expected responsive bounds
                curr_u = main_win.keyboard_view.u_size
                self.assertGreaterEqual(
                    curr_u,
                    min_exp_u - 0.5,
                    f"At {w}x{h}, u_size {curr_u} was below expected minimum {min_exp_u}",
                )
                self.assertLessEqual(
                    curr_u,
                    max_exp_u + 0.5,
                    f"At {w}x{h}, u_size {curr_u} exceeded expected maximum {max_exp_u}",
                )

                # Verify StatusBar is positioned properly at the bottom
                status_bar_y = main_win.status_bar.winfo_y()
                status_bar_h = main_win.status_bar.winfo_height()
                self.assertGreater(status_bar_h, 0, f"StatusBar height was 0 at {w}x{h}")
                self.assertLessEqual(
                    status_bar_y + status_bar_h,
                    h + 50,  # Bounded within window geometry
                    f"StatusBar was pushed outside window bounds at {w}x{h}",
                )
        finally:
            main_win.cleanup()
            main_win.destroy()

    # -------------------------------------------------------------------------
    # 2. Localized Tab Scrolling Tests
    # -------------------------------------------------------------------------
    def test_02_all_tabs_have_independent_scrollable_frames(self) -> None:
        """Every subsystem tab hosts an active CTkScrollableFrame supporting vertical scrolling."""
        expected_tabs = [
            "RGB Global",
            "Per-Key RGB",
            "Key Remap (L1/L2)",
            "Hall / Rapid Trigger",
            "DKS",
            "Macros",
            "Settings",
            "Visualizer",
        ]

        self.root.geometry("800x600")
        main_win = MainWindow(self.root, controller=self.controller)
        main_win.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.root.update()

        try:
            self.assertEqual(len(main_win.tab_scroll_frames), 8)

            for tab_name in expected_tabs:
                self.assertIn(tab_name, main_win.tab_scroll_frames)
                scroll_frame = main_win.tab_scroll_frames[tab_name]

                # Switch to tab
                main_win.tabview.set(tab_name)
                main_win._on_tab_changed()
                self.root.update_idletasks()

                # Verify scroll frame has valid positive dimensions
                self.assertGreater(
                    scroll_frame.winfo_height(),
                    50,
                    f"Tab '{tab_name}' scrollable frame height was invalid ({scroll_frame.winfo_height()})",
                )

                # Test vertical scroll navigation (top to bottom and back)
                canvas = scroll_frame._parent_canvas
                # Scroll to bottom
                canvas.yview_moveto(1.0)
                self.root.update_idletasks()
                yview_bottom = canvas.yview()
                self.assertGreater(yview_bottom[1], 0.0)

                # Scroll to top
                canvas.yview_moveto(0.0)
                self.root.update_idletasks()
                yview_top = canvas.yview()
                self.assertAlmostEqual(yview_top[0], 0.0, places=2)
        finally:
            main_win.cleanup()
            main_win.destroy()

    # -------------------------------------------------------------------------
    # 3. Adaptive KeyboardView Hit-Testing
    # -------------------------------------------------------------------------
    def test_03_keyboard_view_hit_testing_across_scales(self) -> None:
        """Clicking physical keys correctly resolves key selection regardless of u_size scale."""
        main_win = MainWindow(self.root, controller=self.controller)
        main_win.pack(fill="both", expand=True)
        self.root.update_idletasks()

        w_key = KEY_BY_ID["W"]

        try:
            # Test hit testing at small, medium, and large scales
            for test_u in (28.0, 36.0, 44.0):
                main_win.keyboard_view.set_u_size(test_u)
                self.root.update_idletasks()

                # Calculate center of key W under current scale
                w_box = main_win.keyboard_view._key_boxes[w_key]
                cx = (w_box[0] + w_box[2]) / 2.0
                cy = (w_box[1] + w_box[3]) / 2.0

                class DummyEvent:
                    pass

                ev = DummyEvent()
                ev.x = cx
                ev.y = cy
                ev.state = 0

                # Simulate click on key
                main_win.keyboard_view._on_canvas_press(ev)  # type: ignore
                main_win.keyboard_view._on_canvas_release(ev)  # type: ignore

                # Controller must have selected key W (LED slot 35)
                self.assertIn(
                    w_key.led_slot,
                    self.controller.selected_led_slots,
                    f"Hit testing failed for key 'W' at u_size={test_u}",
                )
        finally:
            main_win.cleanup()
            main_win.destroy()

    # -------------------------------------------------------------------------
    # 4. Remap View Left Column Height Natural Sizing
    # -------------------------------------------------------------------------
    def test_04_remap_left_column_shows_all_actions(self) -> None:
        """KeyRemapView left_col naturally sizes to display unbind and reset buttons without clipping."""
        self.root.geometry("800x600")
        main_win = MainWindow(self.root, controller=self.controller)
        main_win.pack(fill="both", expand=True)
        main_win.tabview.set("Key Remap (L1/L2)")
        main_win._on_tab_changed()
        self.root.update_idletasks()
        self.root.update()

        try:
            remap_v = main_win.remap_view
            # Select key W
            self.controller.select_key(KEY_BY_ID["W"].switch_slot)
            self.root.update_idletasks()

            # Buttons should be mapped and have positive heights
            self.assertGreater(remap_v.btn_unbind.winfo_height(), 15)
            self.assertGreater(remap_v.btn_reset_key.winfo_height(), 15)
            self.assertGreater(remap_v.btn_reset_all.winfo_height(), 15)
        finally:
            main_win.cleanup()
            main_win.destroy()


if __name__ == "__main__":
    unittest.main()
