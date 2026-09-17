"""
Unit tests for DKS (Dynamic Keystroke) GUI controller integration.

Focus areas:
- Baseline inspection of physical keys (DKS inactive).
- DKS assignment and two-way Remap Layer 1 coordination (prefix 0x08).
- DKS removal and baseline Remap Layer 1 restoration.
- Protection against non-physical keys and Fn key mutation.
- ProfileWritePlan generation: producing both remap_l1 (AA 22) and dks (AA 28, 19 chunks).
- Closed-loop mock application and dirty state tracking.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.models.dks import DKSEventState, DKSRecord
from keyboard_re.ui.controller import AppController, KeyDKSInfo
from keyboard_re.ui.layout_data import KEY_BY_ID, PHYSICAL_84_SWITCH_SLOTS


class TestDKSController(unittest.TestCase):
    """Test suite for DKS controller integration."""

    def setUp(self):
        self.capture_path = (
            Path(__file__).resolve().parents[2]
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        self.controller = AppController(default_capture_path=self.capture_path)
        self.controller.connect(use_mock=True)

    def test_get_dks_info_baseline(self):
        """In factory baseline, keys have no DKS binding."""
        w_slot = KEY_BY_ID["W"].switch_slot
        info = self.controller.get_dks_info(w_slot)
        self.assertIsNotNone(info)
        self.assertEqual(info.key_id, "W")
        self.assertEqual(info.switch_slot, w_slot)
        self.assertFalse(info.is_active)
        self.assertIsNone(info.dks_slot_index)
        self.assertFalse(info.is_modified)
        self.assertFalse(self.controller.is_key_dks(w_slot))
        self.assertFalse(self.controller.is_key_dks_modified(w_slot))

    def test_set_dks_config_allocates_and_updates_remap_l1(self):
        """Assigning DKS updates both DKSTable and remap_l1 slot with prefix 0x08."""
        w_def = KEY_BY_ID["W"]
        w_slot = w_def.switch_slot

        actions = [0x1A, 0xE1, 0x00, 0x00]  # W, LShift
        states = [
            [DKSEventState.TAP, DKSEventState.HOLD, DKSEventState.OFF, DKSEventState.OFF],
            [DKSEventState.OFF, DKSEventState.HOLD, DKSEventState.OFF, DKSEventState.OFF],
            [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],
            [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],
        ]

        ok = self.controller.set_dks_config(
            switch_slot=w_slot,
            make_value_1_mm=1.5,
            make_value_2_mm=2.5,
            break_value_1_mm=2.5,
            break_value_2_mm=1.5,
            actions=actions,
            states=states,
        )
        self.assertTrue(ok)
        self.assertTrue(self.controller.is_dirty)

        # Inspect updated DKS info
        info = self.controller.get_dks_info(w_slot)
        self.assertIsNotNone(info)
        self.assertTrue(info.is_active)
        self.assertEqual(info.dks_slot_index, 0)
        self.assertTrue(info.is_modified)
        self.assertAlmostEqual(info.make_value_1_mm, 1.5, places=2)
        self.assertAlmostEqual(info.make_value_2_mm, 2.5, places=2)
        self.assertEqual(info.actions[0], 0x1A)
        self.assertEqual(info.actions[1], 0xE1)

        # Check remap Layer 1 slot
        remap_rec = self.controller.working_profile.remap.slots[w_def.remap_slot]
        self.assertTrue(remap_rec.is_dks)
        self.assertEqual(remap_rec.prefix, 0x08)
        self.assertEqual(remap_rec.scancode, 0)  # Slot index 0

    def test_remove_dks_frees_slot_and_restores_remap(self):
        """Removing DKS clears DKSTable slot and restores remap_l1."""
        w_def = KEY_BY_ID["W"]
        w_slot = w_def.switch_slot

        # First assign DKS
        self.controller.set_dks_config(w_slot, actions=[0x1A, 0, 0, 0])
        self.assertTrue(self.controller.is_key_dks(w_slot))

        # Now remove DKS
        ok = self.controller.remove_dks(w_slot)
        self.assertTrue(ok)

        self.assertFalse(self.controller.is_key_dks(w_slot))
        remap_rec = self.controller.working_profile.remap.slots[w_def.remap_slot]
        self.assertFalse(remap_rec.is_dks)
        self.assertNotEqual(remap_rec.prefix, 0x08)

    def test_fn_key_protected_from_dks(self):
        """Fn key (read-only) cannot have DKS assigned."""
        fn_def = KEY_BY_ID["FN"]
        ok = self.controller.set_dks_config(fn_def.switch_slot, actions=[0x04, 0, 0, 0])
        self.assertFalse(ok)
        self.assertFalse(self.controller.is_key_dks(fn_def.switch_slot))

    def test_out_of_bounds_switch_slot_rejected(self):
        """Non-physical switch slot is safely rejected."""
        ok = self.controller.set_dks_config(switch_slot=127, actions=[0x04, 0, 0, 0])
        self.assertFalse(ok)

    def test_profile_write_plan_dks_and_remap(self):
        """Configuring DKS generates both Remap L1 (AA 22) and DKS (AA 28, 19 chunks) in WritePlan."""
        w_slot = KEY_BY_ID["W"].switch_slot
        self.controller.set_dks_config(w_slot, actions=[0x1A, 0, 0, 0])

        plan = self.controller.profile_manager.create_write_plan(
            self.controller.device_state,
            self.controller.working_profile,
        )
        subsystems = [step.subsystem for step in plan.steps]
        self.assertIn("remap_l1", subsystems)
        self.assertIn("dks", subsystems)

        # Check DKS step
        dks_step = plan.get_step("dks")
        self.assertIsNotNone(dks_step)
        self.assertEqual(dks_step.opcode, 0x28)
        self.assertEqual(dks_step.packet_count, 19)

    def test_apply_changes_dks_mock(self):
        """Mock apply verifies full closed-loop synchronization of DKS and Remap L1."""
        w_slot = KEY_BY_ID["W"].switch_slot
        self.controller.set_dks_config(w_slot, actions=[0x1A, 0, 0, 0])
        self.assertTrue(self.controller.is_dirty)

        res = self.controller.apply_changes(confirmed=True)
        self.assertTrue(res.is_success)
        self.assertFalse(self.controller.is_dirty)

        # Verify device_state now has DKS
        self.assertIsNotNone(self.controller.device_state.dks_table_raw)
        self.assertIsNotNone(self.controller.device_state.dks_table)
        rec = self.controller.device_state.dks_table.get_record(0)
        self.assertEqual(rec.actions[0], 0x1A)
