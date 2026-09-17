"""
Unit tests for Key Remap Layer 2 / Fn (AA 16 / AA 26) GUI subsystem and controller logic.

Verifies:
- Layer 2 read/write model invariants (512 bytes, 128 slots)
- Controller layer switching (L1 vs L2)
- Vendor safety protection for Fn Layer (L2):
  - F1..F12 (switch slots 1..12) are strictly read-only
  - Fn key (switch slot 85) is strictly read-only
  - Attempting to set_key_binding or unbind_key on protected keys returns False
- Modifying allowable keys in L2:
  - Byte-exact preservation of untouched slots
  - Remap L1 remains 100% untouched
  - Diff tracking correctly attributes changes to remap_l2 subsystem
- Write plan generation:
  - Produces SubsystemWriteStep with opcode 0x26 and exactly 10 packets
"""

import unittest
from pathlib import Path

from keyboard_re.protocol.keymap import (
    KeyRemapRecord,
    KeymapTable,
)
from keyboard_re.protocol.plan import build_remap_write_chunks
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import (
    FN_DISABLED_SWITCH_SLOTS,
    FN_REMAP_SLOT,
    KEY_BY_ID,
)


class TestKeyRemapLayer2(unittest.TestCase):
    """Verify Layer 2 / Fn remap controller, safety rules, and protocol generation."""

    def setUp(self):
        self.capture_path = (
            Path(__file__).resolve().parents[2]
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        self.controller = AppController(default_capture_path=self.capture_path)
        self.controller.connect(use_mock=True)

    def test_layer_switch_and_defaults(self):
        """Active layer switches between 1 and 2, preserving layer state."""
        self.assertEqual(self.controller.active_remap_layer, 1)
        self.controller.set_active_remap_layer(2)
        self.assertEqual(self.controller.active_remap_layer, 2)

        # Invalid layer rejected
        self.controller.set_active_remap_layer(3)
        self.assertEqual(self.controller.active_remap_layer, 2)

    def test_f1_to_f12_protected_on_layer2(self):
        """F1..F12 (switch slots 1..12) must be read-only in Layer 2."""
        self.controller.set_active_remap_layer(2)

        for slot in FN_DISABLED_SWITCH_SLOTS:
            info = self.controller.get_key_remap_info(switch_slot=slot, layer=2)
            self.assertIsNotNone(info)
            self.assertTrue(
                info.is_readonly,
                f"Slot {slot} should be read-only on Layer 2",
            )

            # Mutate attempts must fail
            self.assertFalse(
                self.controller.set_key_binding(switch_slot=slot, scancode=0x04, layer=2)
            )
            self.assertFalse(self.controller.unbind_key(switch_slot=slot, layer=2))

        # But on Layer 1, F1..F12 are editable
        for slot in FN_DISABLED_SWITCH_SLOTS:
            info_l1 = self.controller.get_key_remap_info(switch_slot=slot, layer=1)
            self.assertFalse(
                info_l1.is_readonly,
                f"Slot {slot} should be editable on Layer 1",
            )

    def test_fn_key_protected_on_both_layers(self):
        """Fn key (switch slot 85) must be read-only on both Layer 1 and Layer 2."""
        for layer in (1, 2):
            info = self.controller.get_key_remap_info(
                switch_slot=FN_REMAP_SLOT, layer=layer
            )
            self.assertTrue(info.is_readonly)
            self.assertFalse(
                self.controller.set_key_binding(
                    switch_slot=FN_REMAP_SLOT, scancode=0x04, layer=layer
                )
            )
            self.assertFalse(
                self.controller.unbind_key(switch_slot=FN_REMAP_SLOT, layer=layer)
            )

    def test_allowable_key_remap_layer2(self):
        """Remap a non-protected key on Layer 2 (e.g. W: switch 34 -> Space 0x2C)."""
        self.controller.set_active_remap_layer(2)

        # Confirm slot 34 is not read-only on L2
        info_before = self.controller.get_key_remap_info(switch_slot=34, layer=2)
        self.assertFalse(info_before.is_readonly)

        # Capture initial states
        l1_before = bytes(self.controller.working_profile.remap.raw_bytes)
        l2_before = bytes(self.controller.working_profile.remap_l2.raw_bytes)

        # Remap W (34) to Space (0x2C) on Layer 2
        success = self.controller.set_key_binding(switch_slot=34, scancode=0x2C, layer=2)
        self.assertTrue(success)

        # Verify Layer 2 record
        rec_l2 = self.controller.working_profile.remap_l2.slots[34]
        self.assertEqual(rec_l2.scancode, 0x2C)
        self.assertEqual(rec_l2.function_type, 0x02)

        # Verify Layer 1 is COMPLETELY UNTOUCHED
        l1_after = bytes(self.controller.working_profile.remap.raw_bytes)
        self.assertEqual(l1_before, l1_after)

        # Verify Layer 2 diff: exactly 4 bytes changed (slot 34 is offset 136..140)
        l2_after = bytes(self.controller.working_profile.remap_l2.raw_bytes)
        self.assertEqual(l2_after[:136], l2_before[:136])
        self.assertEqual(l2_after[136:140], bytes([0x02, 0x00, 0x2C, 0x00]))
        self.assertEqual(l2_after[140:], l2_before[140:])

        # Verify diff accounting
        self.assertTrue(self.controller.is_dirty)
        diff = self.controller.last_diff
        self.assertIsNotNone(diff)

        sub_l1 = diff.get_subsystem("remap_l1")
        self.assertFalse(sub_l1.has_changes)

        sub_l2 = diff.get_subsystem("remap_l2")
        self.assertIsNotNone(sub_l2)
        self.assertTrue(sub_l2.has_changes)
        self.assertEqual(sub_l2.change_count, 1)

    def test_layer2_unbind_and_reset(self):
        """Unbinding a key on Layer 2 sets to zero, and resetting restores baseline."""
        self.controller.set_active_remap_layer(2)

        # Unbind slot 49 (A)
        self.assertTrue(self.controller.unbind_key(switch_slot=49, layer=2))
        rec = self.controller.working_profile.remap_l2.slots[49]
        self.assertEqual(rec.to_bytes(), bytes([0, 0, 0, 0]))
        self.assertTrue(self.controller.is_key_remapped(49, layer=2))

        # Reset slot 49
        self.assertTrue(self.controller.reset_key_to_default(switch_slot=49, layer=2))
        self.assertFalse(self.controller.is_key_remapped(49, layer=2))

    def test_layer2_reset_all(self):
        """Reset all on Layer 2 only resets Layer 2 changes."""
        # Mutate L1
        self.controller.set_key_binding(switch_slot=49, scancode=0x05, layer=1)
        # Mutate L2
        self.controller.set_key_binding(switch_slot=34, scancode=0x2C, layer=2)

        self.assertEqual(self.controller.last_diff.get_subsystem("remap_l1").change_count, 1)
        self.assertEqual(self.controller.last_diff.get_subsystem("remap_l2").change_count, 1)

        # Reset only L2
        self.controller.reset_all_remap(layer=2)
        self.assertEqual(self.controller.last_diff.get_subsystem("remap_l1").change_count, 1)
        self.assertEqual(self.controller.last_diff.get_subsystem("remap_l2").change_count, 0)

    def test_confirmation_summary_generates_aa26_write_step(self):
        """When remap_l2 is modified, dry-run confirmation summary contains AA 26 step."""
        self.controller.set_key_binding(switch_slot=34, scancode=0x2C, layer=2)
        summary = self.controller.get_confirmation_summary()
        self.assertIsNotNone(summary)
        self.assertIn("remap_l2", summary.changed_subsystems)

        l2_steps = [s for s in summary.steps_summary if s["opcode"] == 0x26]
        self.assertEqual(len(l2_steps), 1)
        self.assertEqual(l2_steps[0]["packet_count"], 10)

    def test_build_remap_write_chunks_opcode_0x26(self):
        """build_remap_write_chunks with opcode=0x26 generates 10 chunks matching wire protocol."""
        image = bytes(512)
        chunks = build_remap_write_chunks(image, opcode=0x26)
        self.assertEqual(len(chunks), 10)

        for i, chunk in enumerate(chunks):
            pkt = chunk.packet
            # Prefix AA 26
            self.assertEqual(pkt[0], 0xAA)
            self.assertEqual(pkt[1], 0x26)
            if i < 9:
                self.assertEqual(pkt[2], 0x38)  # 56 bytes
                self.assertEqual(pkt[6], 0x00)  # not last
            else:
                self.assertEqual(pkt[2], 0x08)  # 8 bytes
                self.assertEqual(pkt[3:5], bytes([0xF8, 0x01]))  # offset 504
                self.assertEqual(pkt[6], 0x01)  # isLastPacket


if __name__ == "__main__":
    unittest.main()
