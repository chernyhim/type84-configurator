"""
Unit tests for diff engine and experimental cases for Keys A, S, and D.
"""

import json
import unittest

from keyboard_re.assembler import assemble_packets
from keyboard_re.differ import (
    diff_images,
    diff_summary_by_slot,
    diff_to_dict,
    format_diff_table,
)
from keyboard_re.models import (
    KNOWN_FIELDS,
    ConfidenceLevel,
    ConfigImage,
)
from keyboard_re.parser import parse_capture
from tests.synthetic_fixtures import build_synthetic_capture


class TestDiffer(unittest.TestCase):

    def setUp(self):
        self.base_capture = build_synthetic_capture(description="Base config (1.40mm default)")
        res_base = assemble_packets(parse_capture(self.base_capture))
        self.assertTrue(res_base.success)
        self.base_image = res_base.image

    def test_experimental_case_key_s(self):
        """
        Confirmed experimental case from real hardware capture:
        Key S actuation changed from 1.40mm to 1.39mm.
        Observed change: 0x8C (140) -> 0x8B (139) at absolute address 0x0195.
        Slot: 50 (405 // 8), Offset: +5 (405 % 8).
        """
        mod_capture = build_synthetic_capture(
            custom_overrides={0x0192: 0x8B},
            description="Key S actuation changed to 1.39mm"
        )
        res_mod = assemble_packets(parse_capture(mod_capture))
        self.assertTrue(res_mod.success)

        diffs = diff_images(self.base_image, res_mod.image)

        self.assertEqual(len(diffs), 1, "Exactly one byte must change for isolated Key S actuation change")
        d = diffs[0]

        self.assertEqual(d.absolute_address, 0x0192)
        self.assertEqual(d.absolute_address, 402)
        self.assertEqual(d.slot_index, 50, "Key S must map to slot 50")
        self.assertEqual(d.slot_offset, 2, "Actuation point must be at offset +2 inside slot 50")
        self.assertEqual(d.old_val, 0x8C)
        self.assertEqual(d.new_val, 0x8B)
        self.assertEqual(d.delta, -1)

        self.assertIsNotNone(d.known_field)
        self.assertEqual(d.known_field.key_name, "S")
        self.assertEqual(d.known_field.confidence, ConfidenceLevel.CONFIRMED)

    def test_experimental_case_key_a(self):
        """
        Confirmed experimental case:
        Key A actuation: absolute address 0x018A.
        Slot: 49 (394 // 8), Offset: +2 (394 % 8).
        """
        mod_capture = build_synthetic_capture(
            custom_overrides={0x018A: 0x8B},
            description="Key A actuation changed to 1.39mm"
        )
        res_mod = assemble_packets(parse_capture(mod_capture))
        self.assertTrue(res_mod.success)

        diffs = diff_images(self.base_image, res_mod.image)
        self.assertEqual(len(diffs), 1)
        d = diffs[0]

        self.assertEqual(d.absolute_address, 0x018A)
        self.assertEqual(d.slot_index, 49)
        self.assertEqual(d.slot_offset, 2)
        self.assertEqual(d.old_val, 0x8C)
        self.assertEqual(d.new_val, 0x8B)
        self.assertEqual(d.known_field.key_name, "A")
        self.assertEqual(d.known_field.confidence, ConfidenceLevel.CONFIRMED)

    def test_key_d_mapping(self):
        """
        Key D: adjacent to S in Bank 3 (slot 51, offset 2, address 0x019A).
        """
        known_d = KNOWN_FIELDS.get(0x019A)
        self.assertIsNotNone(known_d)
        self.assertEqual(known_d.key_name, "D")
        self.assertEqual(known_d.slot_index, 51)
        self.assertEqual(known_d.slot_offset, 2)

    def test_consecutive_row_diff(self):
        """Diff when A and S are modified."""
        mod_capture = build_synthetic_capture(
            custom_overrides={0x018A: 0x8B, 0x0192: 0x8B}
        )
        res_mod = assemble_packets(parse_capture(mod_capture))
        diffs = diff_images(self.base_image, res_mod.image)

        self.assertEqual(len(diffs), 2)
        diff_map = {d.absolute_address: d for d in diffs}

        self.assertIn(0x018A, diff_map)
        self.assertEqual(diff_map[0x018A].known_field.key_name, "A")

        self.assertIn(0x0192, diff_map)
        self.assertEqual(diff_map[0x0192].known_field.key_name, "S")


if __name__ == "__main__":
    unittest.main()
