"""
Unit and golden tests for Key Remap Protocol (AA 12, AA 16, AA 1C).
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.protocol.keymap import (
    KEYMAP_BUFFER_SIZE,
    KEYMAP_CHUNK_COUNT,
    KEYMAP_RECORD_SIZE,
    KEYMAP_SLOT_COUNT,
    KeyRemapRecord,
    KeymapTable,
    parse_keymap_chunks,
)
from keyboard_re.protocol.read import parse_calibration_response


class TestKeymapProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        with open(capture_path, "r", encoding="utf-8") as f:
            cls.capture_data = json.load(f)

    def test_key_remap_record_properties(self):
        rec_esc = KeyRemapRecord(prefix=0, scancode=0x29, special=0, function_type=2)
        self.assertFalse(rec_esc.is_empty)
        self.assertTrue(rec_esc.is_standard_key)
        self.assertFalse(rec_esc.is_special_function)
        self.assertEqual(rec_esc.hid_name, "Escape")
        self.assertEqual(rec_esc.to_bytes(), b"\x02\x00\x29\x00")

        rec_empty = KeyRemapRecord(prefix=0, scancode=0, special=0, function_type=0)
        self.assertTrue(rec_empty.is_empty)
        self.assertFalse(rec_empty.is_standard_key)
        self.assertEqual(rec_empty.hid_name, "None")

        rec_fn_arrow = KeyRemapRecord(prefix=0, scancode=0, special=0x10, function_type=0x0D)
        self.assertFalse(rec_fn_arrow.is_empty)
        self.assertFalse(rec_fn_arrow.is_standard_key)
        self.assertTrue(rec_fn_arrow.is_special_function)
        self.assertEqual(rec_fn_arrow.hid_name, "Special_0x10")

    def test_parse_keymap_layer1_golden(self):
        reports_12 = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551238", "551208"))
        ]
        self.assertEqual(len(reports_12), KEYMAP_CHUNK_COUNT)

        table = parse_keymap_chunks(reports_12, expected_opcode=0x12, layer=1)
        self.assertEqual(table.layer, 1)
        self.assertEqual(len(table.raw_bytes), KEYMAP_BUFFER_SIZE)
        self.assertEqual(len(table.slots), KEYMAP_SLOT_COUNT)

        # Bank 0: Function row
        self.assertEqual(table.get_slot(0, 0).hid_name, "Escape")
        self.assertEqual(table.get_slot(0, 1).hid_name, "F1")
        self.assertEqual(table.get_slot(0, 11).hid_name, "F11")
        self.assertEqual(table.get_slot(0, 12).hid_name, "F12")

        # Bank 1: Number row
        self.assertEqual(table.get_slot(1, 0).hid_name, "`~")
        self.assertEqual(table.get_slot(1, 1).hid_name, "1!")
        self.assertEqual(table.get_slot(1, 10).hid_name, "0)")

        # Bank 2: QWERTY row
        self.assertEqual(table.get_slot(2, 0).hid_name, "Tab")
        self.assertEqual(table.get_slot(2, 1).hid_name, "Q")
        self.assertEqual(table.get_slot(2, 2).hid_name, "W")

        # Bank 3: Home row
        self.assertEqual(table.get_slot(3, 0).hid_name, "CapsLock")
        self.assertEqual(table.get_slot(3, 1).hid_name, "A")
        self.assertEqual(table.get_slot(3, 2).hid_name, "S")

        # Bank 4: Shift row
        self.assertEqual(table.get_slot(4, 0).hid_name, "LShift")
        self.assertEqual(table.get_slot(4, 1).hid_name, "Z")

        # Bank 5: Bottom row & navigation
        self.assertEqual(table.get_slot(5, 0).hid_name, "LCtrl")
        self.assertEqual(table.get_slot(5, 1).hid_name, "LGui")
        self.assertEqual(table.get_slot(5, 2).hid_name, "LAlt")
        self.assertEqual(table.get_slot(5, 5).hid_name, "Fn")
        self.assertEqual(table.get_slot(5, 8).hid_name, "LeftArrow")
        self.assertEqual(table.get_slot(5, 9).hid_name, "DownArrow")
        self.assertEqual(table.get_slot(5, 10).hid_name, "UpArrow")
        self.assertEqual(table.get_slot(5, 11).hid_name, "RightArrow")
        self.assertEqual(table.get_slot(5, 12).hid_name, "Backspace")

    def test_parse_keymap_layer2_fn_golden(self):
        reports_16 = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551638", "551608"))
        ]
        self.assertEqual(len(reports_16), KEYMAP_CHUNK_COUNT)

        table = parse_keymap_chunks(reports_16, expected_opcode=0x16, layer=2)
        self.assertEqual(table.layer, 2)

        # F-keys are passthrough / unbound on Fn layer
        for col in range(1, 13):
            self.assertTrue(table.get_slot(0, col).is_empty)

        # Fn + Backspace = ScrollLock (Slot 92 = B5, C12)
        self.assertEqual(table.get_slot(5, 12).hid_name, "ScrollLock")

        # Fn + End = Pause (Slot 107 = B6, C11)
        self.assertEqual(table.get_slot(6, 11).hid_name, "Pause")

        # Fn + LeftArrow has special function code
        rec_left = table.get_slot(5, 8)
        self.assertTrue(rec_left.is_special_function)
        self.assertEqual(rec_left.special, 0x10)

    def test_parse_calibration_response_golden(self):
        reports_1c = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551c38", "551c08"))
        ]
        self.assertEqual(len(reports_1c), KEYMAP_CHUNK_COUNT)

        table = parse_calibration_response(reports_1c)
        self.assertIsInstance(table, KeymapTable)
        self.assertEqual(table.layer, 3)
        self.assertEqual(len(table.raw_bytes), KEYMAP_BUFFER_SIZE)

    def test_bank_column_formula_verified_keys(self):
        reports_12 = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551238", "551208"))
        ]
        table = parse_keymap_chunks(reports_12, expected_opcode=0x12, layer=1)

        # Formula: 1:1 matching hardware matrix
        verified_cases = [
            # key, hall_bank, hall_col, expected_remap_bank, expected_remap_col, expected_hid
            ("ESC", 0, 0, 0, 0, "Escape"),
            ("A", 3, 1, 3, 1, "A"),
            ("Q", 2, 1, 2, 1, "Q"),
            ("ENTER", 4, 12, 4, 12, "Enter"),
            ("LEFT", 5, 8, 5, 8, "LeftArrow"),
            ("UP", 5, 10, 5, 10, "UpArrow"),
            ("BACKSPACE", 5, 12, 5, 12, "Backspace"),
        ]

        for key, h_b, h_c, r_b, r_c, hid in verified_cases:
            self.assertEqual(r_c, h_c, f"Formula failure for key {key}")
            self.assertEqual(r_b, h_b, f"Bank mismatch for key {key}")
            record = table.get_slot(r_b, r_c)
            self.assertEqual(record.hid_name, hid, f"HID usage mismatch for key {key}")


    def test_parse_keymap_validation_errors(self):
        # Wrong report count
        with self.assertRaises(ValueError):
            parse_keymap_chunks([bytes(64)] * 9)

        # Invalid prefix
        bad_reports = [bytes(64)] * 10
        with self.assertRaises(ValueError):
            parse_keymap_chunks(bad_reports)


if __name__ == "__main__":
    unittest.main()
