"""
Unit tests for write plan generator and safety preview engine.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.models.state import KeyboardSnapshot
from keyboard_re.protocol.plan import WritePlan, build_write_plan


class TestPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        cls.baseline = KeyboardSnapshot.load_json(capture_path)

    def test_build_write_plan_hall(self):
        intended_state = self.baseline.clone_mutable()
        intended_state.active_profile.hall.set_actuation("A", 1.39)
        intended_snap = intended_state.to_snapshot()

        plan = build_write_plan(self.baseline, intended_snap)
        self.assertIsInstance(plan, WritePlan)
        self.assertEqual(plan.subsystem, "hall")
        self.assertEqual(plan.profile_id, 1)
        self.assertEqual(len(plan.diffs), 1)
        self.assertEqual(len(plan.packets), 19)

        formatted = plan.format_plan()
        self.assertIn("WRITE PLAN (DRY RUN", formatted)
        self.assertIn("Key A", formatted)
        self.assertIn("1.40 mm -> 1.39 mm", formatted)
        self.assertIn("ZERO HARDWARE TRANSMISSIONS EXECUTED", formatted)

    def test_build_write_plan_noop(self):
        plan = build_write_plan(self.baseline, self.baseline)
        self.assertEqual(plan.subsystem, "none")
        self.assertEqual(len(plan.diffs), 0)
        self.assertEqual(len(plan.packets), 0)


if __name__ == "__main__":
    unittest.main()
