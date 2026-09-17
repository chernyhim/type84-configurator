"""
Forensic regression tests documenting Profile Architecture & Protocol findings.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.models.state import DeviceState


class TestProfileForensics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_path = (
            Path(__file__).resolve().parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.report_path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "PROFILE_SWITCH_FORENSIC_REPORT.md"
        )

    def test_forensic_report_exists(self):
        """Verify that the forensic report is documented in docs/."""
        self.assertTrue(self.report_path.exists())
        content = self.report_path.read_text(encoding="utf-8")
        self.assertIn("GET_GAME_MODE", content)
        self.assertIn("GET_MAGNETIC_AXIS_DKS_DATA", content)
        self.assertIn("DISPROVED", content)

    def test_game_mode_packet_semantics(self):
        """
        Verify that AA 11 (55 11) fields correspond to performance/system parameters:
        - offset 11: sleepTime (1 min)
        - offset 13: reportRate (6 = 8000 Hz)
        - offset 19: stabilityMode (1 = ON)
        - offset 22: autoCalibration (1 = ON)
        """
        with open(self.capture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        status_event = next(
            e for e in data["events"]
            if e.get("direction") == "DEVICE -> HOST" and e.get("data_hex", "").startswith("5511")
        )
        raw = bytes.fromhex(status_event["data_hex"])
        r = raw[8:]  # Payload buffer

        sleep_time = r[3]  # pkt offset 11
        report_rate = r[5]  # pkt offset 13
        stability_mode = r[11]  # pkt offset 19
        auto_calib = r[14]  # pkt offset 22

        self.assertEqual(sleep_time, 1)  # 1 minute sleep timeout
        self.assertEqual(report_rate, 6)  # 8000 Hz report rate
        self.assertEqual(stability_mode, 1)  # Stability Mode enabled
        self.assertEqual(auto_calib, 1)  # Auto calibration enabled

    def test_aa18_is_dks_table(self):
        """
        Verify that AA 18 corresponds to the 1024-byte DKS table (64 slots x 16 bytes),
        which is completely unconfigured (zeros) at factory baseline.
        """
        state = DeviceState.load_json(self.capture_path)
        buffers = state.dump_raw_buffers()

        dks_buffer = buffers.get("dks_table")
        self.assertIsNotNone(dks_buffer)
        self.assertEqual(len(dks_buffer), 1024)  # 18 chunks of 56B + 1 tail chunk of 16B = 1024B
        # At factory baseline, DKS slots are unconfigured (zeros)
        self.assertTrue(all(b == 0 for b in dks_buffer[:1008]))


if __name__ == "__main__":
    unittest.main()
