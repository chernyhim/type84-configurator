"""
Unit and golden tests for Macro / DKS Protocol (AA 15 / 55 15).
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.protocol.macro import (
    MACRO_BUFFER_SIZE,
    MACRO_CHUNK_COUNT,
    MacroTable,
    parse_macro_chunks,
)


class TestMacroProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        with open(capture_path, "r", encoding="utf-8") as f:
            cls.capture_data = json.load(f)

    def test_parse_macro_chunks_golden(self):
        reports_15 = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551538", "551508"))
        ]
        self.assertEqual(len(reports_15), MACRO_CHUNK_COUNT)

        macro_table = parse_macro_chunks(reports_15)
        self.assertIsInstance(macro_table, MacroTable)
        self.assertEqual(macro_table.total_bytes, MACRO_BUFFER_SIZE)
        self.assertFalse(macro_table.has_user_macros)
        self.assertEqual(macro_table.version_flag, 1)

    def test_parse_macro_validation_errors(self):
        with self.assertRaises(ValueError):
            parse_macro_chunks([bytes(64)] * 7)

        bad_reports = [bytes(64)] * 8
        with self.assertRaises(ValueError):
            parse_macro_chunks(bad_reports)


if __name__ == "__main__":
    unittest.main()
