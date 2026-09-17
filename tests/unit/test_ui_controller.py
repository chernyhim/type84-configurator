"""
Unit tests for AppController (Headless GUI Controller & Safe Backend Bridge).

Tests all 10 core safety and functional criteria:
1. Connect and read baseline DeviceState.
2. Working profile creation from baseline.
3. RGB editing modifies only working profile.
4. DeviceState remains strictly unchanged on UI edits.
5. Dirty flag appears on modifications.
6. Diff contains rgb_global changes.
7. Apply without confirmation rejects and executes 0 writes.
8. After confirmed mocked apply, baseline is updated and dirty flag resets.
9. Custom effect 0x80 remains a valid selectable UI effect.
10. Firmware runtime aliases 0x14 and 0x15 do NOT appear in the user effect catalog.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.applicator import ExecutionStatus
from keyboard_re.protocol.rgb import (
    EFFECT_BREATHING,
    EFFECT_CUSTOM,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    RGB_EFFECT_CATALOG,
)
from keyboard_re.protocol.transport import MockHidTransport
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import (
    KEY_BY_ID,
    KEY_BY_LED_SLOT,
    KEY_BY_SWITCH_SLOT,
    TYPE84_LAYOUT,
)


class TestAppController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import customtkinter as ctk
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.controller = AppController()
        # Connect using mock session
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self):
        self.controller.disconnect()

    def test_1_connect_and_read_state(self):
        """Connect reads baseline DeviceState and initializes connection state."""
        self.assertTrue(self.controller.is_connected)
        self.assertTrue(self.controller.is_mock)
        self.assertIsNotNone(self.controller.device_state)
        self.assertIsNotNone(self.controller.working_profile)
        self.assertIn("Connected", self.controller.connection_status_text)
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

    def test_2_working_profile_created_from_baseline(self):
        """Working profile is cloned from baseline DeviceState with identical settings."""
        baseline = self.controller.device_state
        profile = self.controller.working_profile

        self.assertEqual(profile.profile_id, 1)
        self.assertEqual(profile.rgb_global.effect, baseline.rgb_global.effect)
        self.assertEqual(profile.rgb_global.brightness, baseline.rgb_global.brightness)
        self.assertEqual(profile.rgb_global.speed, baseline.rgb_global.speed)

        # Ensure object isolation (different memory addresses)
        self.assertIsNot(profile.rgb_global, baseline.rgb_global)
        self.assertIsNot(profile.remap, baseline.remap_l1)

    def test_3_and_4_edit_rgb_modifies_only_working_profile_and_leaves_baseline_unchanged(self):
        """UI edits mutate ONLY working profile; baseline DeviceState remains byte-identical."""
        baseline_effect = self.controller.device_state.rgb_global.effect
        self.assertEqual(baseline_effect, EFFECT_RIPPLE_SPREAD)  # 0x0F in capture

        # Change effect in working profile
        self.controller.set_rgb_effect(EFFECT_STATIC)

        # Working profile updated
        self.assertEqual(self.controller.working_profile.rgb_global.effect, EFFECT_STATIC)

        # Baseline DeviceState strictly unchanged
        self.assertEqual(self.controller.device_state.rgb_global.effect, EFFECT_RIPPLE_SPREAD)

    def test_5_and_6_dirty_flag_and_diff_contain_rgb_global(self):
        """Modification triggers dirty flag, incrementing diff_count and detailing rgb_global."""
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

        # Change brightness
        self.controller.set_rgb_brightness(2)

        self.assertTrue(self.controller.is_dirty)
        self.assertGreater(self.controller.diff_count, 0)
        self.assertIsNotNone(self.controller.last_diff)
        self.assertTrue(self.controller.last_diff.subsystems["rgb_global"].has_changes)

        # Verify confirmation summary can be generated
        summary = self.controller.get_confirmation_summary()
        self.assertIsNotNone(summary)
        self.assertIn("rgb_global", summary.changed_subsystems)
        self.assertEqual(summary.total_packets, 1)

    def test_7_apply_without_confirmation_does_not_execute_write(self):
        """Calling apply_changes with confirmed=False aborts with zero writes."""
        self.controller.set_rgb_effect(EFFECT_BREATHING)
        self.assertTrue(self.controller.is_dirty)

        mock_transport: MockHidTransport = self.controller.transport
        self.assertEqual(len(mock_transport.recorded_reports), 0)

        result = self.controller.apply_changes(confirmed=False)

        self.assertEqual(result.status, ExecutionStatus.ABORTED)
        self.assertFalse(result.is_success)
        # Strict guarantee: 0 writes occurred
        self.assertEqual(len(mock_transport.recorded_reports), 0)
        # Still dirty
        self.assertTrue(self.controller.is_dirty)

    def test_8_after_successful_mocked_apply_dirty_flag_resets(self):
        """After successful apply, baseline updates to new state and dirty flag resets."""
        self.controller.set_rgb_effect(EFFECT_STATIC)
        self.controller.set_rgb_brightness(4)
        self.assertTrue(self.controller.is_dirty)

        result = self.controller.apply_changes(confirmed=True)

        self.assertTrue(result.is_success)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

        # Baseline is now synchronized with working profile
        self.assertEqual(self.controller.device_state.rgb_global.effect, EFFECT_STATIC)
        self.assertEqual(self.controller.device_state.rgb_global.brightness, 4)

        # Dirty flag reset to False
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

    def test_9_custom_effect_0x80_is_valid_ui_effect(self):
        """Custom effect 0x80 is present in UI effects list and can be set."""
        effects = self.controller.get_rgb_effects()
        effect_ids = [e_id for e_id, _, _ in effects]

        self.assertIn(EFFECT_CUSTOM, effect_ids)
        self.assertIn(0x80, effect_ids)

        # Set custom effect in controller
        self.controller.set_rgb_effect(EFFECT_CUSTOM)
        self.assertEqual(self.controller.working_profile.rgb_global.effect, EFFECT_CUSTOM)

    def test_10_0x14_and_0x15_do_not_appear_in_user_catalog(self):
        """Firmware runtime status codes 0x14/0x15 are strictly absent from UI effects."""
        effects = self.controller.get_rgb_effects()
        effect_ids = [e_id for e_id, _, _ in effects]

        self.assertNotIn(0x14, effect_ids)
        self.assertNotIn(0x15, effect_ids)
        self.assertNotIn(0x14, RGB_EFFECT_CATALOG)
        self.assertNotIn(0x15, RGB_EFFECT_CATALOG)
        self.assertEqual(len(RGB_EFFECT_CATALOG), 26)

    def test_layout_data_integrity(self):
        """Layout data contains all 84 keys with clean switch_slot vs led_slot separation."""
        self.assertEqual(len(TYPE84_LAYOUT), 84)

        # Verify key W
        key_w = KEY_BY_ID["W"]
        self.assertEqual(key_w.switch_slot, 34)
        self.assertEqual(key_w.led_slot, 35)

        # Verify key Esc
        key_esc = KEY_BY_ID["ESC"]
        self.assertEqual(key_esc.switch_slot, 0)
        self.assertEqual(key_esc.led_slot, 0)

        # Verify all 84 switch slots are unique
        switch_slots = [k.switch_slot for k in TYPE84_LAYOUT]
        self.assertEqual(len(switch_slots), len(set(switch_slots)))

        # Verify all 84 led slots are unique
        led_slots = [k.led_slot for k in TYPE84_LAYOUT]
        self.assertEqual(len(led_slots), len(set(led_slots)))

    def test_listener_notification_on_change(self):
        """Subscribed UI listeners are notified on state changes."""
        notified = False

        def callback():
            nonlocal notified
            notified = True

        self.controller.subscribe(callback)
        self.controller.set_rgb_brightness(1)
        self.assertTrue(notified)

        # Unsubscribe works
        notified = False
        self.controller.unsubscribe(callback)
        self.controller.set_rgb_brightness(2)
        self.assertFalse(notified)

    def test_layout_zero_overlaps_and_right_cluster_geometry(self):
        """Verify 84 physical keys, 0 overlaps, and exact right navigation cluster alignment."""
        self.assertEqual(len(TYPE84_LAYOUT), 84)

        # 1. Check for 0 overlaps across all 84 keys
        for i in range(len(TYPE84_LAYOUT)):
            for j in range(i + 1, len(TYPE84_LAYOUT)):
                k1 = TYPE84_LAYOUT[i]
                k2 = TYPE84_LAYOUT[j]
                # Intersection check
                overlaps = not (
                    k1.x + k1.width <= k2.x
                    or k2.x + k2.width <= k1.x
                    or k1.y + k1.height <= k2.y
                    or k2.y + k2.height <= k1.y
                )
                self.assertFalse(
                    overlaps,
                    msg=f"Key overlap detected between {k1.key_id} and {k2.key_id}",
                )

        # 2. Right navigation cluster exact geometry verification
        key_map = {k.key_id: k for k in TYPE84_LAYOUT}

        # Row 0: Print | Home | End
        prt = key_map["PRTSC"]
        home = key_map["HOME"]
        end = key_map["END"]
        self.assertEqual((prt.row, prt.y, prt.x, prt.width), (0, 0.0, 14.0, 1.0))
        self.assertEqual((home.row, home.y, home.x, home.width), (0, 0.0, 15.5, 1.0))
        self.assertEqual((end.row, end.y, end.x, end.width), (0, 0.0, 16.5, 1.0))

        # Row 1: Backspace | Ins | PgUp
        back = key_map["BACKSPACE"]
        ins = key_map["INS"]
        pgup = key_map["PGUP"]
        self.assertEqual((back.row, back.y, back.x, back.width), (1, 1.0, 13.0, 2.0))
        self.assertEqual((ins.row, ins.y, ins.x, ins.width), (1, 1.0, 15.5, 1.0))
        self.assertEqual((pgup.row, pgup.y, pgup.x, pgup.width), (1, 1.0, 16.5, 1.0))

        # Row 2: Backslash | Del | PgDn
        bslash = key_map["BACKSLASH"]
        del_k = key_map["DEL"]
        pgdn = key_map["PGDN"]
        self.assertEqual((bslash.row, bslash.y, bslash.x, bslash.width), (2, 2.0, 13.5, 1.5))
        self.assertEqual((del_k.row, del_k.y, del_k.x, del_k.width), (2, 2.0, 15.5, 1.0))
        self.assertEqual((pgdn.row, pgdn.y, pgdn.x, pgdn.width), (2, 2.0, 16.5, 1.0))

        # Row 3: Enter ends at 15.0u, nav area is empty
        enter = key_map["ENTER"]
        self.assertEqual((enter.row, enter.y, enter.x, enter.width), (3, 3.0, 12.75, 2.25))

        # Row 4: Up arrow in middle column
        up = key_map["UP"]
        self.assertEqual((up.row, up.y, up.x, up.width), (4, 4.0, 15.5, 1.0))

        # Row 5: Left | Down | Right
        left = key_map["LEFT"]
        down = key_map["DOWN"]
        right = key_map["RIGHT"]
        self.assertEqual((left.row, left.y, left.x, left.width), (5, 5.0, 14.5, 1.0))
        self.assertEqual((down.row, down.y, down.x, down.width), (5, 5.0, 15.5, 1.0))
        self.assertEqual((right.row, right.y, right.x, right.width), (5, 5.0, 16.5, 1.0))

        # Middle column alignment: Home, Ins, Del, Up, Down all at x=15.5
        self.assertEqual(home.x, ins.x)
        self.assertEqual(ins.x, del_k.x)
        self.assertEqual(del_k.x, up.x)
        self.assertEqual(up.x, down.x)

        # Right column alignment: End, PgUp, PgDn, Right all at x=16.5
        self.assertEqual(end.x, pgup.x)
        self.assertEqual(pgup.x, pgdn.x)
        self.assertEqual(pgdn.x, right.x)

    def test_confirmation_effect_formatting(self):
        """Confirmation dialog formats effect values with human readable names and safe fallbacks."""
        from keyboard_re.ui.views.dialogs import _format_param_value

        # Known catalog effects
        self.assertEqual(_format_param_value("effect", 0x01), "0x01 (Static Always On)")
        self.assertEqual(_format_param_value("effect", 0x0F), "0x0F (Ripple Spread)")
        self.assertEqual(_format_param_value("effect", 0x80), "0x80 (Custom Per-Key)")

        # Unknown effect fallback
        self.assertEqual(_format_param_value("effect", 0x99), "0x99 (Unknown)")
        self.assertEqual(_format_param_value("effect", "99"), "0x63 (Unknown)")

        # Other parameters remain untouched
        self.assertEqual(_format_param_value("brightness", 5), "5")
        self.assertEqual(_format_param_value("speed", 2), "2")

    def test_color_chooser_cancel_and_select(self):
        """Color chooser applies color when selected and does nothing when cancelled."""
        from unittest.mock import patch
        import customtkinter as ctk
        from keyboard_re.ui.views.rgb_global_view import RGBGlobalView

        view = RGBGlobalView(self.root, controller=self.controller)
        try:
            initial_hex = view.hex_entry.get()
            initial_primary = self.controller.working_profile.rgb_global.primary

            # 1. Cancel case
            with patch("tkinter.colorchooser.askcolor", return_value=(None, None)):
                view._on_pick_color()

            self.assertEqual(view.hex_entry.get(), initial_hex)
            self.assertEqual(self.controller.working_profile.rgb_global.primary, initial_primary)

            # 2. Select case
            with patch("tkinter.colorchooser.askcolor", return_value=((0, 255, 0), "#00FF00")):
                view._on_pick_color()

            self.assertEqual(view.hex_entry.get(), "#00FF00")
            self.assertEqual(self.controller.working_profile.rgb_global.primary, (0, 255, 0))
            self.assertTrue(self.controller.is_dirty)
        finally:
            view.destroy()

    def test_connecting_state_feedback_and_error_handling(self):
        """Connect USB updates button text to Connecting... and handles errors gracefully."""
        from unittest.mock import patch
        from keyboard_re.ui.views.main_window import MainWindow

        window = MainWindow(self.root, controller=self.controller)
        try:
            self.controller.disconnect()
            self.assertEqual(window.connect_btn.cget("state"), "normal")

            # 1. Failure path: controller.connect() returns False
            with patch.object(self.controller, "connect", return_value=False), \
                 patch("keyboard_re.ui.views.main_window.ErrorDialog"):
                window._on_connect_physical()
                self.assertEqual(window.connect_btn.cget("text"), "Connect USB")
                self.assertEqual(window.connect_btn.cget("state"), "normal")

            # 2. Success path: controller.connect() returns True
            with patch.object(self.controller, "connect", return_value=True):
                window._on_connect_physical()
                self.assertEqual(window.connect_btn.cget("text"), "Connect USB")
                self.assertEqual(window.connect_btn.cget("state"), "disabled")
                self.assertEqual(window.mock_btn.cget("state"), "disabled")
                self.assertEqual(window.disconnect_btn.cget("state"), "normal")
        finally:
            window.destroy()

    def test_rgb_effects_display_names_clean_without_hex_prefix(self):
        """User effect display names must NOT contain hex prefixes like '0x0F: '."""
        effects = self.controller.get_rgb_effects()
        self.assertEqual(len(effects), len(RGB_EFFECT_CATALOG))
        for effect_id, display_name, meta in effects:
            # Display name must not start with 0x
            self.assertFalse(display_name.startswith("0x"), f"Effect name has hex prefix: {display_name}")
            # Display name format: Name (Russian Name)
            self.assertEqual(display_name, f"{meta.name_en} ({meta.name_ru})")

    def test_set_rgb_color_mode_no_attribute_error(self):
        """Toggling color mode (0=Single, 1=Rainbow) must not raise AttributeError."""
        initial_mode = self.controller.working_profile.rgb_global.color_mode
        opposite_mode = 0 if initial_mode == 1 else 1

        # 1. Switch to opposite mode -> must trigger dirty flag
        self.controller.set_rgb_color_mode(opposite_mode)
        self.assertEqual(self.controller.working_profile.rgb_global.color_mode, opposite_mode)
        self.assertTrue(self.controller.is_dirty)

        # 2. Switch back to initial mode -> dirty flag resets
        self.controller.set_rgb_color_mode(initial_mode)
        self.assertEqual(self.controller.working_profile.rgb_global.color_mode, initial_mode)
        self.assertFalse(self.controller.is_dirty)

    def test_drag_selection_api_set_and_add(self):
        """set_led_selection and add_leds_to_selection update selection and filter reserved slots."""
        w_led = KEY_BY_ID["W"].led_slot   # 35
        a_led = KEY_BY_ID["A"].led_slot   # 50
        s_led = KEY_BY_ID["S"].led_slot   # 51

        # Replace selection via set_led_selection
        self.controller.set_led_selection({w_led, a_led, 30, 100})  # 30, 100 are non-physical
        self.assertEqual(self.controller.selected_led_slots, {w_led, a_led})

        # Add to selection via add_leds_to_selection
        self.controller.add_leds_to_selection({s_led, 62, 127})     # 62, 127 are non-physical
        self.assertEqual(self.controller.selected_led_slots, {w_led, a_led, s_led})

        # Attempting to set only non-physical slots leaves selection unchanged
        self.controller.set_led_selection({30, 62, 64})
        self.assertEqual(self.controller.selected_led_slots, {w_led, a_led, s_led})


if __name__ == "__main__":
    unittest.main()
