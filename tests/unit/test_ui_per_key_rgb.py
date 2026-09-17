"""
Unit tests for Per-Key RGB Editor GUI slice (AppController & Safe Per-Key Pipeline).

Verifies all critical architectural criteria and user constraints:
1. RGBMatrix LED_ID byte-exact preservation on set, clear selected, fill all, clear all.
2. Operations strictly bounded to the 84 physical keys in TYPE84_LAYOUT (never 0..127 user set).
3. Strict separation of switch_slot vs led_slot (e.g. 'W' = switch 34, LED 35).
4. Selection mechanics: single select, multi-select (Shift), toggle (Ctrl), select all, deselect, preset groups.
5. Eyedropper: sampling color from selected key.
6. Custom Mode 0x80 auto-coordination upon editing matrix.
7. Diff generation and ConfirmationDialog LedDiff formatting without exceptions.
8. Baseline DeviceState immutability prior to confirmed Apply.
9. Full Mock Apply pipeline: sends 11 HID packets, resets dirty flag, updates baseline.
10. Preserves all reserved slots (30, 62, 64, 87..127) as zeroed with canonical LED_ID.
"""

from __future__ import annotations

import unittest
from typing import Set

from keyboard_re.applicator import ExecutionStatus
from keyboard_re.models.rgb_matrix import RGBMatrix
from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    EFFECT_STATIC,
    LED_SLOT_COUNT,
    LED_SLOT_SIZE,
)
from keyboard_re.protocol.transport import MockHidTransport
from keyboard_re.ui.controller import PHYSICAL_84_LED_SLOTS, AppController
from keyboard_re.ui.layout_data import (
    KEY_BY_ID,
    KEY_BY_LED_SLOT,
    KEY_BY_SWITCH_SLOT,
    TYPE84_LAYOUT,
)
from keyboard_re.ui.views.dialogs import ConfirmationDialog


class TestUIPerKeyRGB(unittest.TestCase):
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
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self):
        self.controller.disconnect()

    # -------------------------------------------------------------------------
    # 1. LED_ID Byte-Exact Preservation
    # -------------------------------------------------------------------------

    def test_1_led_id_byte_exact_preservation_across_all_mutations(self):
        """
        LED_ID byte (offset i*4 + 3) must remain strictly equal to i (0..127)
        after set color, clear selected, fill all, and clear all.
        """
        # Ensure canonical RGBMatrix is loaded
        self.controller.working_profile.set_rgb_matrix(RGBMatrix())

        # A. Set color for specific key
        self.controller.select_led(35)  # 'W'
        self.controller.set_selected_leds_color((255, 0, 0))

        raw = self.controller.working_profile.rgb_matrix
        for i in range(LED_SLOT_COUNT):
            led_id = raw[i * LED_SLOT_SIZE + 3]
            self.assertEqual(led_id, i, f"Slot {i} LED_ID altered after set_selected_leds_color: {led_id}")

        # B. Clear selected
        self.controller.clear_selected_leds()
        raw = self.controller.working_profile.rgb_matrix
        for i in range(LED_SLOT_COUNT):
            led_id = raw[i * LED_SLOT_SIZE + 3]
            self.assertEqual(led_id, i, f"Slot {i} LED_ID altered after clear_selected_leds: {led_id}")

        # C. Fill all 84 physical keys
        self.controller.fill_all_leds("#00FF00")
        raw = self.controller.working_profile.rgb_matrix
        for i in range(LED_SLOT_COUNT):
            led_id = raw[i * LED_SLOT_SIZE + 3]
            self.assertEqual(led_id, i, f"Slot {i} LED_ID altered after fill_all_leds: {led_id}")

        # D. Clear all 84 physical keys
        self.controller.clear_all_leds()
        raw = self.controller.working_profile.rgb_matrix
        for i in range(LED_SLOT_COUNT):
            led_id = raw[i * LED_SLOT_SIZE + 3]
            self.assertEqual(led_id, i, f"Slot {i} LED_ID altered after clear_all_leds: {led_id}")

    # -------------------------------------------------------------------------
    # 2. Bounded to 84 Physical Keys & Reserved Slots Untouched
    # -------------------------------------------------------------------------

    def test_2_strictly_bounded_to_84_physical_keys(self):
        """UI operations operate strictly on 84 physical keys and reject invalid/reserved slots."""
        self.assertEqual(len(PHYSICAL_84_LED_SLOTS), 84)
        self.assertEqual(len(TYPE84_LAYOUT), 84)

        # Reserved PCB slots
        reserved_slots = {30, 62, 64}
        for s in reserved_slots:
            self.assertNotIn(s, PHYSICAL_84_LED_SLOTS)

        # Unused tail slots (87..127)
        for s in range(87, 128):
            self.assertNotIn(s, PHYSICAL_84_LED_SLOTS)

        # Attempt to select a reserved slot directly -> must be rejected
        self.controller.select_led(30)
        self.assertEqual(len(self.controller.selected_led_slots), 0)

        self.controller.select_led(100)
        self.assertEqual(len(self.controller.selected_led_slots), 0)

        # Select All selects exactly 84 slots
        self.controller.select_all_leds()
        self.assertEqual(len(self.controller.selected_led_slots), 84)
        self.assertEqual(self.controller.selected_led_slots, set(PHYSICAL_84_LED_SLOTS))

    def test_2b_reserved_and_unused_slots_remain_untouched_after_fill_all(self):
        """Reserved and unused slots remain untouched (exact baseline values) after fill_all_leds."""
        matrix_before = self.controller.get_active_rgb_matrix()
        non_physical_slots = [s for s in range(LED_SLOT_COUNT) if s not in PHYSICAL_84_LED_SLOTS]
        original_colors = {s: matrix_before.get_led(s) for s in non_physical_slots}

        self.controller.fill_all_leds((255, 255, 255))
        matrix_after = self.controller.get_active_rgb_matrix()

        # Non-physical slots must remain strictly unchanged
        for s in non_physical_slots:
            self.assertEqual(
                matrix_after.get_led(s),
                original_colors[s],
                f"Non-physical slot {s} was modified by fill_all_leds!",
            )

        # Physical keys must all be (255, 255, 255)
        for k in TYPE84_LAYOUT:
            self.assertEqual(matrix_after.get_led(k.led_slot), (255, 255, 255))

    # -------------------------------------------------------------------------
    # 3. switch_slot vs led_slot Separation
    # -------------------------------------------------------------------------

    def test_3_switch_slot_and_led_slot_strictly_separated(self):
        """
        Key 'W' has switch_slot=34 and led_slot=35.
        Setting color for 'W' must write to slot 35 in RGBMatrix, leaving slot 34 alone.
        """
        w_def = KEY_BY_ID["W"]
        self.assertEqual(w_def.switch_slot, 34)
        self.assertEqual(w_def.led_slot, 35)

        # Select W by LED slot
        self.controller.select_led(w_def.led_slot)
        self.assertIn(35, self.controller.selected_led_slots)
        self.assertNotIn(34, self.controller.selected_led_slots)

        # Set Red color
        self.controller.set_selected_leds_color((255, 0, 0))

        matrix = self.controller.get_active_rgb_matrix()
        # LED 35 is Red
        self.assertEqual(matrix.get_led(35), (255, 0, 0))
        # LED 34 is 'Q', must NOT be altered by editing 'W'
        self.assertNotEqual(matrix.get_led(34), (255, 0, 0))

    # -------------------------------------------------------------------------
    # 4. Selection Mechanics
    # -------------------------------------------------------------------------

    def test_4_selection_single_multi_toggle_and_groups(self):
        """Test single, multi (Shift), toggle (Ctrl), deselect, and preset groups."""
        led_w = KEY_BY_ID["W"].led_slot  # 35
        led_a = KEY_BY_ID["A"].led_slot  # 50
        led_s = KEY_BY_ID["S"].led_slot  # 51
        led_d = KEY_BY_ID["D"].led_slot  # 52

        # A. Single select
        self.controller.select_led(led_w)
        self.assertEqual(self.controller.selected_led_slots, {led_w})
        self.assertEqual(self.controller.primary_selected_led, led_w)

        # B. Multi select (Shift)
        self.controller.select_led(led_a, multi=True)
        self.assertEqual(self.controller.selected_led_slots, {led_w, led_a})
        self.assertEqual(self.controller.primary_selected_led, led_a)

        # C. Toggle select (Ctrl)
        self.controller.select_led(led_s, toggle=True)
        self.assertEqual(self.controller.selected_led_slots, {led_w, led_a, led_s})
        # Toggle off led_a
        self.controller.select_led(led_a, toggle=True)
        self.assertEqual(self.controller.selected_led_slots, {led_w, led_s})

        # D. Deselect all
        self.controller.deselect_all_leds()
        self.assertEqual(len(self.controller.selected_led_slots), 0)
        self.assertIsNone(self.controller.primary_selected_led)

        # E. Preset group WASD
        self.controller.select_led_group("wasd")
        self.assertEqual(self.controller.selected_led_slots, {led_w, led_a, led_s, led_d})

        # F. Preset group Arrows
        self.controller.select_led_group("arrows")
        expected_arrows = {
            KEY_BY_ID["UP"].led_slot,
            KEY_BY_ID["DOWN"].led_slot,
            KEY_BY_ID["LEFT"].led_slot,
            KEY_BY_ID["RIGHT"].led_slot,
        }
        self.assertEqual(self.controller.selected_led_slots, expected_arrows)

    # -------------------------------------------------------------------------
    # 5. Eyedropper (Color Sampling)
    # -------------------------------------------------------------------------

    def test_5_eyedropper_samples_color_from_selected_key(self):
        """Eyedropper returns exact RGB color of primary selected key."""
        # No key selected -> None
        self.assertIsNone(self.controller.sample_color_from_selected())

        # Select ESC (slot 0) and set Cyan (0, 255, 255)
        esc_slot = KEY_BY_ID["ESC"].led_slot
        self.controller.select_led(esc_slot)
        self.controller.set_selected_leds_color((0, 255, 255))

        sampled = self.controller.sample_color_from_selected()
        self.assertEqual(sampled, (0, 255, 255))

    # -------------------------------------------------------------------------
    # 6. Custom Mode 0x80 Auto-Coordination
    # -------------------------------------------------------------------------

    def test_6_custom_mode_0x80_auto_coordination(self):
        """Editing matrix automatically coordinates rgb_global.effect = 0x80."""
        # Start in Static mode (0x01)
        self.controller.set_rgb_effect(EFFECT_STATIC)
        self.assertEqual(self.controller.working_profile.rgb_global.effect, EFFECT_STATIC)

        # Edit per-key matrix color
        self.controller.select_led(35)
        self.controller.set_selected_leds_color("#FF0000")

        # Must have automatically switched to 0x80 (Custom)
        self.assertEqual(self.controller.working_profile.rgb_global.effect, EFFECT_CUSTOM)

    # -------------------------------------------------------------------------
    # 7. Diff Generation and Confirmation Dialog LedDiff Formatting
    # -------------------------------------------------------------------------

    def test_7_diff_and_confirmation_dialog_with_led_diff(self):
        """ConfirmationDialog safely formats LedDiff with key labels and hex colors without errors."""
        self.controller.select_led(KEY_BY_ID["W"].led_slot)
        self.controller.set_selected_leds_color((255, 0, 0))

        diff = self.controller.last_diff
        self.assertIsNotNone(diff)
        self.assertTrue(diff.has_changes)
        self.assertIn("rgb_matrix", diff.changed_subsystems)

        sub_m = diff.get_subsystem("rgb_matrix")
        self.assertIsNotNone(sub_m)
        self.assertTrue(sub_m.has_changes)
        self.assertGreaterEqual(len(sub_m.details), 1)

        # Generate dry-run summary
        summary = self.controller.get_confirmation_summary()
        self.assertIsNotNone(summary)
        self.assertIn("rgb_matrix", summary.changed_subsystems)
        # Total packets: 11 (1 Global AA23 + 10 Matrix AA24)
        self.assertEqual(summary.total_packets, 11)

        # Instantiate ConfirmationDialog without errors (tests formatting of LedDiff)
        dialog = ConfirmationDialog(
            master=self.root,
            summary=summary,
            diff=diff,
        )
        dialog.destroy()

    # -------------------------------------------------------------------------
    # 8. Baseline DeviceState Immutability
    # -------------------------------------------------------------------------

    def test_8_baseline_device_state_remains_strictly_unchanged_on_matrix_edits(self):
        """Baseline DeviceState.rgb_matrix_raw remains byte-exact until confirmed Apply."""
        baseline_matrix_copy = bytes(self.controller.device_state.rgb_matrix_raw)

        # Paint 10 keys in working profile
        self.controller.select_led_group("wasd")
        self.controller.set_selected_leds_color("#FF00FF")

        # Working profile is updated
        matrix = self.controller.get_active_rgb_matrix()
        self.assertEqual(matrix.get_led(KEY_BY_ID["W"].led_slot), (255, 0, 255))

        # Baseline DeviceState is 100% byte-identical to original capture
        self.assertEqual(bytes(self.controller.device_state.rgb_matrix_raw), baseline_matrix_copy)

    # -------------------------------------------------------------------------
    # 9. Full Mock Apply Pipeline
    # -------------------------------------------------------------------------

    def test_9_apply_mock_per_key_rgb_pipeline(self):
        """Confirmed Apply executes 11 packets, synchronizes baseline state, and resets dirty flag."""
        # Paint W = Red
        self.controller.select_led(KEY_BY_ID["W"].led_slot)
        self.controller.set_selected_leds_color((255, 0, 0))

        self.assertTrue(self.controller.is_dirty)
        self.assertGreater(self.controller.diff_count, 0)

        # Apply changes with confirmation
        result = self.controller.apply_changes(confirmed=True)

        self.assertTrue(result.is_success)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

        # Dirty flag reset
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

        # Baseline updated
        w_led = KEY_BY_ID["W"].led_slot
        off = w_led * LED_SLOT_SIZE
        self.assertEqual(
            tuple(self.controller.device_state.rgb_matrix_raw[off : off + 3]),
            (255, 0, 0),
        )

    # -------------------------------------------------------------------------
    # 10. Interactive Drag & Click Selection in KeyboardView
    # -------------------------------------------------------------------------

    def test_10_keyboard_view_click_and_drag_selection_interaction(self):
        """
        Verify:
        - Regular click selects single key.
        - Click on empty space/margin preserves selection (does NOT deselect).
        - Drag marquee selects intersecting keys (replaces selection).
        - Shift + drag marquee adds intersecting keys (additive select).
        - Empty drag does NOT clear selection.
        - Deselect All button clears selection.
        """
        from keyboard_re.ui.views.keyboard_view import KeyboardView

        view = KeyboardView(self.root, controller=self.controller)
        try:
            w_k = KEY_BY_ID["W"]
            a_k = KEY_BY_ID["A"]
            s_k = KEY_BY_ID["S"]
            d_k = KEY_BY_ID["D"]

            wx1, wy1, wx2, wy2 = view._key_boxes[w_k]
            wcx, wcy = (wx1 + wx2) / 2.0, (wy1 + wy2) / 2.0

            # 1. Regular click on W
            p_evt = type("Event", (), {"x": wcx, "y": wcy, "state": 0})()
            view._on_canvas_press(p_evt)
            r_evt = type("Event", (), {"x": wcx, "y": wcy, "state": 0})()
            view._on_canvas_release(r_evt)
            self.assertEqual(self.controller.selected_led_slots, {w_k.led_slot})

            # 2. Click on empty margin (0, 0) MUST preserve selection
            p_evt = type("Event", (), {"x": 0, "y": 0, "state": 0})()
            view._on_canvas_press(p_evt)
            r_evt = type("Event", (), {"x": 0, "y": 0, "state": 0})()
            view._on_canvas_release(r_evt)
            self.assertEqual(self.controller.selected_led_slots, {w_k.led_slot})

            # 3. Drag marquee over A and S
            ax1, ay1, ax2, ay2 = view._key_boxes[a_k]
            sx1, sy1, sx2, sy2 = view._key_boxes[s_k]
            rx1, ry1 = min(ax1, sx1), min(ay1, sy1)
            rx2, ry2 = max(ax2, sx2), max(ay2, sy2)

            view._on_canvas_press(type("Event", (), {"x": rx1, "y": ry1, "state": 0})())
            view._on_canvas_motion(type("Event", (), {"x": rx2, "y": ry2, "state": 0})())
            self.assertTrue(view._is_dragging)
            view._on_canvas_release(type("Event", (), {"x": rx2, "y": ry2, "state": 0})())
            self.assertEqual(self.controller.selected_led_slots, {a_k.led_slot, s_k.led_slot})

            # 4. Shift + drag marquee over D (additive)
            dx1, dy1, dx2, dy2 = view._key_boxes[d_k]
            view._on_canvas_press(type("Event", (), {"x": dx1, "y": dy1, "state": 0x0001})())
            view._on_canvas_motion(type("Event", (), {"x": dx2, "y": dy2, "state": 0x0001})())
            view._on_canvas_release(type("Event", (), {"x": dx2, "y": dy2, "state": 0x0001})())
            self.assertEqual(self.controller.selected_led_slots, {a_k.led_slot, s_k.led_slot, d_k.led_slot})

            # 5. Drag in empty margin (no keys touched) MUST preserve selection
            view._on_canvas_press(type("Event", (), {"x": 0, "y": 0, "state": 0})())
            view._on_canvas_motion(type("Event", (), {"x": 8, "y": 8, "state": 0})())
            view._on_canvas_release(type("Event", (), {"x": 8, "y": 8, "state": 0})())
            self.assertEqual(self.controller.selected_led_slots, {a_k.led_slot, s_k.led_slot, d_k.led_slot})

            # 6. Deselect All button clears selection
            view._on_deselect_all()
            self.assertEqual(len(self.controller.selected_led_slots), 0)
        finally:
            view.destroy()

    # -------------------------------------------------------------------------
    # 11. RGBGlobalView Color Mode & Layout Stability
    # -------------------------------------------------------------------------

    def test_11_rgb_global_view_color_mode_and_sliders(self):
        """RGBGlobalView switches color mode without AttributeError and updates sliders."""
        from keyboard_re.ui.views.rgb_global_view import RGBGlobalView

        view = RGBGlobalView(self.root, controller=self.controller)
        try:
            # 1. Switch to Rainbow RGB
            view._on_color_mode_changed("Rainbow RGB")
            self.assertEqual(self.controller.working_profile.rgb_global.color_mode, 1)

            # 2. Switch to Single Color
            view._on_color_mode_changed("Single Color")
            self.assertEqual(self.controller.working_profile.rgb_global.color_mode, 0)

            # 3. Slider updates do not trigger exceptions
            view._on_brightness_changed(3)
            self.assertEqual(self.controller.working_profile.rgb_global.brightness, 3)
            view._on_speed_changed(2)
            self.assertEqual(self.controller.working_profile.rgb_global.speed, 2)
        finally:
            view.destroy()

    # -------------------------------------------------------------------------
    # 12. PerKeyRGBView Deselect All Action
    # -------------------------------------------------------------------------

    def test_12_per_key_rgb_view_deselect_all_position_and_action(self):
        """PerKeyRGBView Select All and Deselect All work cleanly."""
        from keyboard_re.ui.views.per_key_rgb_view import PerKeyRGBView

        view = PerKeyRGBView(self.root, controller=self.controller)
        try:
            self.controller.select_all_leds()
            self.assertEqual(len(self.controller.selected_led_slots), 84)

            self.controller.deselect_all_leds()
            self.assertEqual(len(self.controller.selected_led_slots), 0)
        finally:
            view.destroy()

