"""
Unit tests for Hall Effect / Rapid Trigger (AA 17 / AA 27) GUI controller subsystem.

Verifies:
- Inspection of single key Hall parameters and default values.
- Inspection of multi-key selections, uniform vs mixed state detection.
- Parameter modification with strict field isolation and range clamping [0.10..4.00 mm].
- Rapid Trigger enabled/disabled derived strictly from press/release sensitivities (0.00 = OFF).
- Preservation of unknown flags (+6..+7) verbatim without mutation.
- Key and global resets back to baseline device state.
- Out-of-bounds slot protection (strictly restricted to 84 physical keys).
- Dry-run write plan generation (19 packets of opcode 0x27 for subsystem hall).
- End-to-end mock apply with closed-loop readback verification.
"""

from pathlib import Path
import unittest

from keyboard_re.models.base import KEY_RECORD_SIZE, KeyConfig, key_address
from keyboard_re.ui.controller import AppController, KeyHallInfo, MultiKeyHallInfo
from keyboard_re.ui.layout_data import (
    KEY_BY_ID,
    KEY_BY_SWITCH_SLOT,
    PHYSICAL_84_SWITCH_SLOTS,
    TYPE84_LAYOUT,
)


class TestHallController(unittest.TestCase):
    """Unit test suite for Hall / Rapid Trigger controller operations."""

    def setUp(self):
        self.capture_path = (
            Path(__file__).resolve().parents[2]
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        self.controller = AppController(default_capture_path=self.capture_path)
        self.controller.connect(use_mock=True)

    def test_get_hall_info_baseline_key(self):
        """Inspect a physical key in baseline state (A: switch slot 49)."""
        info = self.controller.get_hall_info(switch_slot=49)
        self.assertIsNotNone(info)
        self.assertEqual(info.key_id, "A")
        self.assertEqual(info.switch_slot, 49)
        self.assertAlmostEqual(info.actuation_mm, 1.40, places=2)
        self.assertAlmostEqual(info.rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(info.rt_release_mm, 0.00, places=2)
        self.assertFalse(info.is_rt_enabled)
        self.assertFalse(info.is_modified)
        self.assertAlmostEqual(info.default_actuation_mm, 1.40, places=2)
        self.assertAlmostEqual(info.default_rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(info.default_rt_release_mm, 0.00, places=2)

    def test_get_multi_hall_info_uniform(self):
        """Inspect WASD in baseline: uniform values across all 4 keys."""
        wasd_slots = {
            KEY_BY_ID["W"].switch_slot,
            KEY_BY_ID["A"].switch_slot,
            KEY_BY_ID["S"].switch_slot,
            KEY_BY_ID["D"].switch_slot,
        }
        multi = self.controller.get_multi_hall_info(wasd_slots)
        self.assertEqual(multi.count, 4)
        self.assertFalse(multi.has_mixed_actuation)
        self.assertFalse(multi.has_mixed_rt)
        self.assertFalse(multi.is_any_modified)
        self.assertAlmostEqual(multi.actuation_mm, 1.40, places=2)
        self.assertAlmostEqual(multi.rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(multi.rt_release_mm, 0.00, places=2)
        self.assertFalse(multi.is_rt_enabled)

    def test_get_multi_hall_info_mixed(self):
        """Inspect WASD when W has different actuation and RT enabled: detects mixed states."""
        w_slot = KEY_BY_ID["W"].switch_slot
        wasd_slots = {
            w_slot,
            KEY_BY_ID["A"].switch_slot,
            KEY_BY_ID["S"].switch_slot,
            KEY_BY_ID["D"].switch_slot,
        }

        # Modify W
        self.controller.set_hall_parameters(
            {w_slot},
            actuation_mm=0.80,
            rt_press_mm=0.15,
            rt_release_mm=0.15,
        )

        multi = self.controller.get_multi_hall_info(wasd_slots)
        self.assertEqual(multi.count, 4)
        self.assertTrue(multi.has_mixed_actuation)
        self.assertTrue(multi.has_mixed_rt)
        self.assertTrue(multi.is_any_modified)
        self.assertIsNone(multi.actuation_mm)
        self.assertIsNone(multi.rt_press_mm)
        self.assertIsNone(multi.rt_release_mm)
        self.assertIsNone(multi.is_rt_enabled)

    def test_set_hall_parameters_field_isolation(self):
        """Updating actuation leaves RT and flags untouched; updating RT leaves actuation untouched."""
        slot = KEY_BY_ID["A"].switch_slot
        b = slot // 16
        c = slot % 16
        addr = key_address(b, c)

        # Precondition flags
        raw_before = self.controller.working_profile.hall.raw_image[addr : addr + 8]
        flags_before = KeyConfig.from_bytes(raw_before).flags

        # 1. Update only actuation
        success = self.controller.set_hall_parameters({slot}, actuation_mm=2.50)
        self.assertTrue(success)

        info = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(info.actuation_mm, 2.50, places=2)
        self.assertAlmostEqual(info.rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(info.rt_release_mm, 0.00, places=2)
        self.assertEqual(info.flags, flags_before)
        self.assertTrue(info.is_modified)

        # 2. Update only RT press
        success2 = self.controller.set_hall_parameters({slot}, rt_press_mm=0.35)
        self.assertTrue(success2)

        info2 = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(info2.actuation_mm, 2.50, places=2)
        self.assertAlmostEqual(info2.rt_press_mm, 0.35, places=2)
        self.assertAlmostEqual(info2.rt_release_mm, 0.00, places=2)
        self.assertTrue(info2.is_rt_enabled)
        self.assertEqual(info2.flags, flags_before | 0x01)

        # 3. Update only RT release
        success3 = self.controller.set_hall_parameters({slot}, rt_release_mm=0.20)
        self.assertTrue(success3)

        info3 = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(info3.actuation_mm, 2.50, places=2)
        self.assertAlmostEqual(info3.rt_press_mm, 0.35, places=2)
        self.assertAlmostEqual(info3.rt_release_mm, 0.20, places=2)
        self.assertTrue(info3.is_rt_enabled)
        self.assertEqual(info3.flags, flags_before | 0x01)

    def test_rt_enable_disable_derived_state(self):
        """Rapid Trigger toggle enables/disables via canonical wire flag (flags & 0x01)."""
        slot = KEY_BY_ID["SPACE"].switch_slot
        b = slot // 16
        c = slot % 16
        addr = key_address(b, c)
        flags_before = KeyConfig.from_bytes(
            self.controller.working_profile.hall.raw_image[addr : addr + 8]
        ).flags

        # Enable RT
        self.controller.set_hall_rt_enabled({slot}, enabled=True, default_press=0.15, default_release=0.15)
        info = self.controller.get_hall_info(slot)
        self.assertTrue(info.is_rt_enabled)
        self.assertAlmostEqual(info.rt_press_mm, 0.15, places=2)
        self.assertAlmostEqual(info.rt_release_mm, 0.15, places=2)
        self.assertEqual(info.flags, flags_before | 0x01)

        # Disable RT
        self.controller.set_hall_rt_enabled({slot}, enabled=False)
        info_disabled = self.controller.get_hall_info(slot)
        self.assertFalse(info_disabled.is_rt_enabled)
        self.assertAlmostEqual(info_disabled.rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(info_disabled.rt_release_mm, 0.00, places=2)
        self.assertEqual(info_disabled.flags, flags_before & ~0x01)

    def test_parameter_clamping(self):
        """Actuation clamped to [0.10..4.00], RT clamped to [0.00..4.00]."""
        slot = KEY_BY_ID["A"].switch_slot

        # Under-range actuation
        self.controller.set_hall_parameters({slot}, actuation_mm=0.01)
        self.assertAlmostEqual(self.controller.get_hall_info(slot).actuation_mm, 0.10, places=2)

        # Over-range actuation
        self.controller.set_hall_parameters({slot}, actuation_mm=5.50)
        self.assertAlmostEqual(self.controller.get_hall_info(slot).actuation_mm, 4.00, places=2)

        # Negative RT
        self.controller.set_hall_parameters({slot}, rt_press_mm=-0.50, rt_release_mm=-1.00)
        info = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(info.rt_press_mm, 0.00, places=2)
        self.assertAlmostEqual(info.rt_release_mm, 0.00, places=2)

        # Over-range RT
        self.controller.set_hall_parameters({slot}, rt_press_mm=9.99, rt_release_mm=4.50)
        info2 = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(info2.rt_press_mm, 4.00, places=2)
        self.assertAlmostEqual(info2.rt_release_mm, 4.00, places=2)

    def test_reset_selected_keys(self):
        """Resetting a single key restores baseline and keeps other modified keys modified."""
        slot_a = KEY_BY_ID["A"].switch_slot
        slot_w = KEY_BY_ID["W"].switch_slot

        self.controller.set_hall_parameters({slot_a}, actuation_mm=2.00)
        self.controller.set_hall_parameters({slot_w}, actuation_mm=3.00)

        self.assertTrue(self.controller.is_key_hall_modified(slot_a))
        self.assertTrue(self.controller.is_key_hall_modified(slot_w))
        self.assertTrue(self.controller.is_dirty)

        # Reset A only
        self.controller.reset_hall_keys({slot_a})
        self.assertFalse(self.controller.is_key_hall_modified(slot_a))
        self.assertTrue(self.controller.is_key_hall_modified(slot_w))
        self.assertTrue(self.controller.is_dirty)

    def test_reset_all_hall(self):
        """Reset All restores entire 84-key Hall state to baseline."""
        wasd = {KEY_BY_ID[k].switch_slot for k in ("W", "A", "S", "D")}
        self.controller.set_hall_parameters(wasd, actuation_mm=0.50, rt_press_mm=0.20, rt_release_mm=0.20)

        self.assertTrue(self.controller.is_dirty)
        self.controller.reset_all_hall()

        self.assertFalse(self.controller.is_dirty)
        for s in wasd:
            self.assertFalse(self.controller.is_key_hall_modified(s))
            info = self.controller.get_hall_info(s)
            self.assertAlmostEqual(info.actuation_mm, 1.40, places=2)
            self.assertFalse(info.is_rt_enabled)

    def test_out_of_bounds_switch_slot_rejected(self):
        """Non-physical switch slots are strictly ignored/rejected."""
        res = self.controller.set_hall_parameters({999, -1, 127}, actuation_mm=2.00)
        self.assertFalse(res)
        self.assertFalse(self.controller.is_dirty)

    def test_write_plan_generation_hall(self):
        """Modifying Hall parameter builds 19-chunk write plan with opcode 0x27."""
        slot = KEY_BY_ID["A"].switch_slot
        self.controller.set_hall_parameters({slot}, actuation_mm=2.00)

        summary = self.controller.get_confirmation_summary()
        self.assertIsNotNone(summary)
        self.assertIn("hall", summary.changed_subsystems)
        self.assertEqual(summary.total_packets, 19)

        step = summary.steps_summary[0]
        self.assertEqual(step["subsystem"], "hall")
        self.assertEqual(step["opcode"], 0x27)
        self.assertEqual(step["packet_count"], 19)

    def test_apply_changes_mock_verification(self):
        """Full mock apply executes, updates baseline device state, and passes readback."""
        slot = KEY_BY_ID["A"].switch_slot
        self.controller.set_hall_parameters({slot}, actuation_mm=2.00, rt_press_mm=0.20, rt_release_mm=0.20)
        self.assertTrue(self.controller.is_dirty)

        result = self.controller.apply_changes(confirmed=True)
        self.assertTrue(result.is_success)
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

        # Baseline is now updated to the applied settings
        base_info = self.controller.get_hall_info(slot)
        self.assertAlmostEqual(base_info.actuation_mm, 2.00, places=2)
        self.assertTrue(base_info.is_rt_enabled)
        self.assertFalse(base_info.is_modified)


if __name__ == "__main__":
    unittest.main()
