"""
Unit tests for Hall Flags research runner and image manipulation utilities.
"""

from pathlib import Path
import struct
import unittest

from keyboard_re.models.base import CONFIG_IMAGE_SIZE, key_address
from keyboard_re.research.hall_flags import (
    HallFlagExperimentResult,
    SINGLE_BITS,
    build_flag_image,
    format_flags_summary_table,
    verify_flag_diff,
)


class TestHallFlagsResearch(unittest.TestCase):
    def test_single_bits_constant(self):
        """Verify all 16 bits (0..15) are represented accurately."""
        self.assertEqual(len(SINGLE_BITS), 16)
        for i in range(16):
            self.assertEqual(SINGLE_BITS[i], 1 << i)

    def test_build_flag_image_modifies_only_target_flags(self):
        """Verify build_flag_image touches ONLY bytes 0x0193..0x0194 for Key A."""
        base_img = bytearray(CONFIG_IMAGE_SIZE)
        # Put Key A default state: Actuation 1.40 mm, RT 0.00 mm, Flags 0
        addr_a = key_address(3, 1)  # 0x018D
        base_img[addr_a : addr_a + 8] = struct.pack("<4H", 140, 0, 0, 0)

        # Modify bit 0: flags = 0x0001
        mod_img = build_flag_image(base_img, "A", 0x0001)
        self.assertEqual(len(mod_img), CONFIG_IMAGE_SIZE)

        # Check diff
        changed, unexpected = verify_flag_diff(base_img, mod_img, addr_a + 6)
        self.assertEqual(changed, [addr_a + 6])
        self.assertEqual(unexpected, [])
        self.assertEqual(mod_img[addr_a + 6 : addr_a + 8], b"\x01\x00")

        # Verify Actuation and RT are completely intact
        self.assertEqual(mod_img[addr_a : addr_a + 2], struct.pack("<H", 140))
        self.assertEqual(mod_img[addr_a + 2 : addr_a + 4], b"\x00\x00")
        self.assertEqual(mod_img[addr_a + 4 : addr_a + 6], b"\x00\x00")

    def test_verify_flag_diff_detects_unexpected(self):
        """Verify verify_flag_diff flags any corruption in other offsets."""
        base = bytearray(CONFIG_IMAGE_SIZE)
        mod = bytearray(CONFIG_IMAGE_SIZE)
        flags_addr = 0x0193

        # Normal modification: offset 0x0193
        mod[flags_addr] = 0x01
        changed, unexpected = verify_flag_diff(base, mod, flags_addr)
        self.assertEqual(changed, [flags_addr])
        self.assertEqual(unexpected, [])

        # Corrupted modification: offset 0x0193 AND offset 0x0100
        mod[0x0100] = 0xFF
        changed, unexpected = verify_flag_diff(base, mod, flags_addr)
        self.assertIn(flags_addr, changed)
        self.assertIn(0x0100, changed)
        self.assertEqual(unexpected, [0x0100])

    def test_format_flags_summary_table(self):
        """Verify report table formatting."""
        res = HallFlagExperimentResult(
            bit_index=0,
            flag_value=0x0001,
            bytes_le=b"\x01\x00",
            description="Bit 0 (0x0001)",
            changed_offsets=[0x0193],
            unexpected_offsets=[],
            read_back_value=0x0001,
            write_status="VERIFIED",
            read_back_status="VERIFIED",
            restore_status="VERIFIED",
            physical_behavior="Normal (keystroke registers as expected)",
            semantic_status="CONFIRMED (storage verified)",
        )
        table = format_flags_summary_table([res])
        self.assertIn("Bit 0 (0x0001)", table)
        self.assertIn("VERIFIED", table)
        self.assertIn("0x0193", table)
        self.assertIn("CONFIRMED", table)


if __name__ == "__main__":
    unittest.main()
