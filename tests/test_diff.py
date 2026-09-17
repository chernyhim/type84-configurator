"""
Unit tests for universal diff engine (StateDiff & compare_snapshots).
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.models.state import KeyboardSnapshot
from keyboard_re.protocol.diff import StateDiff, compare_snapshots
from keyboard_re.protocol.keymap import KeyRemapRecord


class TestDiffEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        cls.baseline = KeyboardSnapshot.load_json(capture_path)

    def test_diff_noop(self):
        diffs = self.baseline.diff(self.baseline)
        self.assertEqual(len(diffs), 0)

    def test_diff_single_key_actuation(self):
        modified_state = self.baseline.clone_mutable()
        modified_state.active_profile.hall.set_actuation("A", 1.39)
        modified_snapshot = modified_state.to_snapshot()

        diffs = self.baseline.diff(modified_snapshot)
        self.assertEqual(len(diffs), 1)

        d = diffs[0]
        self.assertEqual(d.subsystem, "hall")
        self.assertEqual(d.target, "Key A")
        self.assertEqual(d.parameter, "actuation")
        self.assertEqual(d.old_value, "1.40 mm")
        self.assertEqual(d.new_value, "1.39 mm")
        self.assertEqual(d.address, 0x018A)
        self.assertEqual(d.old_bytes, b"\x8c\x00")
        self.assertEqual(d.new_bytes, b"\x8b\x00")

        # Test formatting
        text = d.format_text()
        self.assertIn("[HALL]", text)
        self.assertIn("Key A", text)
        self.assertIn("1.40 mm -> 1.39 mm", text)

    def test_diff_inverse(self):
        # 1.40 -> 1.39
        state_139 = self.baseline.clone_mutable()
        state_139.active_profile.hall.set_actuation("A", 1.39)
        snap_139 = state_139.to_snapshot()

        # Diff from 1.39 back to 1.40
        inv_diffs = snap_139.diff(self.baseline)
        self.assertEqual(len(inv_diffs), 1)
        self.assertEqual(inv_diffs[0].old_value, "1.39 mm")
        self.assertEqual(inv_diffs[0].new_value, "1.40 mm")
        self.assertEqual(inv_diffs[0].old_bytes, b"\x8b\x00")
        self.assertEqual(inv_diffs[0].new_bytes, b"\x8c\x00")

    def test_diff_multiple_keys(self):
        mod_state = self.baseline.clone_mutable()
        mod_state.active_profile.hall.set_actuation("A", 1.39)
        mod_state.active_profile.hall.set_rt("S", 0.15, 0.25)
        mod_snap = mod_state.to_snapshot()

        diffs = self.baseline.diff(mod_snap)
        # 1 for A (actuation), 3 for S (rt_press, rt_release, and flags from enable_rt)
        self.assertEqual(len(diffs), 4)
        params = {d.parameter for d in diffs}
        self.assertEqual(params, {"actuation", "rt_press", "rt_release", "flags"})

    def test_diff_keymap_change(self):
        mod_state = self.baseline.clone_mutable()
        # Slot 49 is Key A. Change it to B (0x05)
        mod_state.active_profile.keymap.slots[49] = KeyRemapRecord(
            prefix=0, scancode=0x05, special=0, function_type=2
        )
        mod_snap = mod_state.to_snapshot()

        diffs = self.baseline.diff(mod_snap)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].subsystem, "keymap_l1")
        self.assertIn("A", diffs[0].old_value)
        self.assertIn("B", diffs[0].new_value)

    def test_diff_rgb_global_change(self):
        mod_state = self.baseline.clone_mutable()
        if mod_state.active_profile.rgb_global:
            mod_state.active_profile.rgb_global.brightness = 3
        mod_snap = mod_state.to_snapshot()

        diffs = self.baseline.diff(mod_snap)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].subsystem, "rgb_global")
        self.assertEqual(diffs[0].parameter, "brightness")
        self.assertEqual(diffs[0].old_value, 5)
        self.assertEqual(diffs[0].new_value, 3)


if __name__ == "__main__":
    unittest.main()
