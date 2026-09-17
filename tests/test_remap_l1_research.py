"""
Unit tests for Key Remap L1 (AA 12) research, slot mapping, and record structures.
"""

from pathlib import Path
import struct
import unittest

from keyboard_re.protocol.keymap import (
    KEYMAP_BUFFER_SIZE,
    KEYMAP_RECORD_SIZE,
    KEYMAP_SLOT_COUNT,
    KeyRemapRecord,
    parse_keymap_chunks,
)
from keyboard_re.research.remap_l1 import (
    analyze_key_a_to_b,
    format_remap_analysis_report,
    hall_to_remap_slot,
    key_to_remap_slot,
    parse_remap_buffer,
)


class TestRemapL1Research(unittest.TestCase):
    def test_key_a_slot_calculation(self):
        """Verify Key A (Bank 3, Col 1) maps to Remap Slot 49 (offset 196 = 0x00C4)."""
        remap_bank, remap_col, slot, offset = hall_to_remap_slot(3, 1)
        self.assertEqual(remap_bank, 3)
        self.assertEqual(remap_col, 1)
        self.assertEqual(slot, 49)
        self.assertEqual(offset, 196)
        self.assertEqual(f"0x{offset:04X}", "0x00C4")

        # Via key name
        h_bank, h_col, h_addr, r_bank, r_col, r_slot, r_off = key_to_remap_slot("A")
        self.assertEqual((h_bank, h_col), (3, 1))
        self.assertEqual(h_addr, 0x0188)
        self.assertEqual((r_bank, r_col, r_slot, r_off), (3, 1, 49, 196))

    def test_priority_keys_slot_calculations(self):
        """Verify Remap slot calculations for other priority keys."""
        # ESC: Hall (0, 0) -> Remap (0, 0) -> Slot 0, offset 0
        _, _, s_esc, off_esc = hall_to_remap_slot(0, 0)
        self.assertEqual((s_esc, off_esc), (0, 0))

        # Q: Hall (2, 1) -> Remap (2, 1) -> Slot 33, offset 132
        _, _, s_q, off_q = hall_to_remap_slot(2, 1)
        self.assertEqual((s_q, off_q), (33, 132))

        # Enter: Hall (4, 12) -> Remap (4, 12) -> Slot 76, offset 304
        _, _, s_enter, off_enter = hall_to_remap_slot(4, 12)
        self.assertEqual((s_enter, off_enter), (76, 304))

        # Space: Hall (5, 3) -> Remap (5, 3) -> Slot 83, offset 332
        _, _, s_space, off_space = hall_to_remap_slot(5, 3)
        self.assertEqual((s_space, off_space), (83, 332))

        # Backspace: Hall (5, 12) -> Remap (5, 12) -> Slot 92, offset 368
        _, _, s_bs, off_bs = hall_to_remap_slot(5, 12)
        self.assertEqual((s_bs, off_bs), (92, 368))

    def test_key_remap_record_encode_decode(self):
        """Verify 4-byte KeyRemapRecord serialization on physical wire."""
        rec = KeyRemapRecord.for_standard_key(0x04)
        raw = rec.to_bytes()
        self.assertEqual(raw, bytes([0x02, 0x00, 0x04, 0x00]))
        self.assertEqual(rec.hid_name, "A")
        self.assertTrue(rec.is_standard_key)
        self.assertFalse(rec.is_empty)

        # Unpack
        restored = KeyRemapRecord.from_bytes(raw)
        self.assertEqual(restored, rec)

    def test_parse_remap_buffer(self):
        """Verify 512-byte buffer parses into 128 slots."""
        buf = bytearray(KEYMAP_BUFFER_SIZE)
        # Put Key A in Slot 49 (offset 196)
        buf[196:200] = bytes([0x02, 0x00, 0x04, 0x00])

        slots = parse_remap_buffer(buf)
        self.assertEqual(len(slots), KEYMAP_SLOT_COUNT)
        self.assertEqual(slots[49].scancode, 0x04)
        self.assertEqual(slots[49].hid_name, "A")
        self.assertTrue(slots[0].is_empty)

    def test_analyze_key_a_to_b_diff_isolation(self):
        """Verify modifying A -> B changes strictly 1 byte (offset 198: 0x04 -> 0x05)."""
        buf = bytearray(KEYMAP_BUFFER_SIZE)
        # Put Key A in Slot 49 (offset 196)
        buf[196:200] = bytes([0x02, 0x00, 0x04, 0x00])

        analysis = analyze_key_a_to_b(buf)
        self.assertEqual(analysis["remap_slot"], 49)
        self.assertEqual(analysis["byte_offset"], 196)
        self.assertEqual(analysis["current_bytes"], bytes([0x02, 0x00, 0x04, 0x00]))
        self.assertEqual(analysis["target_bytes"], bytes([0x02, 0x00, 0x05, 0x00]))

        # Diff must be exactly 1 byte: offset 198
        diff = analysis["diff"]
        self.assertEqual(len(diff), 1)
        addr, old_b, new_b = diff[0]
        self.assertEqual(addr, 198)  # 0x00C6
        self.assertEqual(old_b, 0x04)
        self.assertEqual(new_b, 0x05)

    def test_baseline_file_integrity_if_present(self):
        """Verify live captured baseline file if already saved."""
        p = Path("scratch/remap_l1_vendor_normalized.bin")
        if not p.exists():
            p = Path("captures/research/remap_l1/a_to_b_before.bin")
        if p.exists():
            data = p.read_bytes()
            self.assertEqual(len(data), KEYMAP_BUFFER_SIZE)
            if p.name == "remap_l1_vendor_normalized.bin":
                slot_49 = data[196:200]
                self.assertEqual(slot_49, bytes([0x02, 0x00, 0x04, 0x00]))


if __name__ == "__main__":
    unittest.main()
