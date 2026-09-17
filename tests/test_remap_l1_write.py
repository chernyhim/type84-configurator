"""
Unit tests for Remap L1 Write capture analyzer and packet dissector.
"""

import json
from pathlib import Path
import struct
import unittest

from keyboard_re.research.remap_l1_write import (
    PacketEvent,
    analyze_remap_write_capture,
    format_remap_write_report,
    isolate_remap_write_session,
)


class TestRemapL1WriteAnalyzer(unittest.TestCase):
    def test_isolate_write_session(self):
        """Verify write burst isolation filters only candidate write packets."""
        events = [
            PacketEvent(0, 0.0, "HOST -> DEVICE", "sendReport", 0, bytes.fromhex("AA10300000000100"), 8),
            PacketEvent(1, 10.0, "DEVICE -> HOST", "inputreport", 0, bytes.fromhex("5510300000000100"), 8),
            # Write burst candidate
            PacketEvent(2, 50.0, "HOST -> DEVICE", "sendReport", 0, bytes.fromhex("AA22380000" + "00" * 56), 61),
            PacketEvent(3, 55.0, "DEVICE -> HOST", "inputreport", 0, bytes.fromhex("5522380000" + "00" * 56), 61),
            PacketEvent(4, 60.0, "HOST -> DEVICE", "sendReport", 0, bytes.fromhex("AA22383800" + "00" * 56), 61),
            PacketEvent(5, 65.0, "DEVICE -> HOST", "inputreport", 0, bytes.fromhex("5522383800" + "00" * 56), 61),
        ]

        isolated = isolate_remap_write_session(events)
        self.assertEqual(len(isolated), 4)
        self.assertEqual(isolated[0].index, 2)
        self.assertEqual(isolated[-1].index, 5)

    def test_simulated_remap_write_analysis(self):
        """Simulate a synthetic 10-chunk remap write and verify reassembly and diff."""
        baseline = bytearray(512)
        # Key A in Slot 50 (offset 200)
        baseline[200:204] = bytes([0x00, 0x04, 0x00, 0x02])

        # Target modified buffer: A -> B (0x05)
        modified = bytearray(baseline)
        modified[201] = 0x05

        # Construct synthetic packets
        synthetic_events = []
        # 9 chunks of 56 bytes
        for i in range(9):
            addr = i * 56
            payload = modified[addr : addr + 56]
            pkt = bytes([0xAA, 0x22, 0x38]) + struct.pack("<H", addr) + payload
            pkt_padded = pkt + bytes(64 - len(pkt))
            synthetic_events.append({
                "index": len(synthetic_events),
                "timestamp_ms": i * 15.0,
                "direction": "HOST -> DEVICE",
                "event_type": "sendReport",
                "report_id": 0,
                "data_hex": pkt_padded.hex(),
            })
            # Simulated ACK
            ack = bytes([0x55, 0x22, 0x38]) + struct.pack("<H", addr) + payload
            synthetic_events.append({
                "index": len(synthetic_events),
                "timestamp_ms": i * 15.0 + 3.0,
                "direction": "DEVICE -> HOST",
                "event_type": "inputreport",
                "report_id": 0,
                "data_hex": (ack + bytes(64 - len(ack))).hex(),
            })

        # Tail chunk: 8 bytes at 504
        tail_payload = modified[504:512]
        tail_pkt = bytes([0xAA, 0x22, 0x08]) + struct.pack("<H", 504) + tail_payload
        synthetic_events.append({
            "index": len(synthetic_events),
            "timestamp_ms": 150.0,
            "direction": "HOST -> DEVICE",
            "event_type": "sendReport",
            "report_id": 0,
            "data_hex": (tail_pkt + bytes(64 - len(tail_pkt))).hex(),
        })
        tail_ack = bytes([0x55, 0x22, 0x08]) + struct.pack("<H", 504) + tail_payload
        synthetic_events.append({
            "index": len(synthetic_events),
            "timestamp_ms": 153.0,
            "direction": "DEVICE -> HOST",
            "event_type": "inputreport",
            "report_id": 0,
            "data_hex": (tail_ack + bytes(64 - len(tail_ack))).hex(),
        })

        # Save temporary synthetic capture
        tmp_capture = Path("captures/research/remap_l1/test_synthetic_write.json")
        tmp_capture.parent.mkdir(parents=True, exist_ok=True)
        tmp_capture.write_text(json.dumps({"events": synthetic_events}), encoding="utf-8")

        try:
            analysis = analyze_remap_write_capture(tmp_capture, baseline_bytes=bytes(baseline))
            self.assertEqual(analysis.write_opcode, "0x22")
            self.assertEqual(analysis.chunks_count, 10)
            self.assertEqual(analysis.acks_count, 10)
            self.assertTrue(analysis.a_to_b_confirmed)
            self.assertEqual(len(analysis.baseline_diffs), 1)
            self.assertEqual(analysis.baseline_diffs[0], (201, 0x04, 0x05))

            report = format_remap_write_report(analysis)
            self.assertIn("Key A -> B Confirmed: YES", report)
            self.assertIn("0x22", report)
        finally:
            if tmp_capture.exists():
                tmp_capture.unlink()

    def test_real_remap_write_capture(self):
        """Verify real official capture captures/experiments/remap_write_a_to_b.json."""
        capture_p = Path("captures/experiments/remap_write_a_to_b.json")
        if not capture_p.exists():
            self.skipTest("remap_write_a_to_b.json not found")

        baseline_p = Path("captures/research/remap_l1/a_to_b_before.bin")
        baseline = baseline_p.read_bytes() if baseline_p.exists() else None

        analysis = analyze_remap_write_capture(capture_p, baseline_bytes=baseline)
        self.assertEqual(analysis.write_opcode, "0x22")
        self.assertEqual(analysis.report_id, 0)
        self.assertEqual(analysis.report_length, 64)
        self.assertEqual(analysis.chunks_count, 10)
        self.assertEqual(analysis.acks_count, 10)
        self.assertTrue(analysis.a_to_b_confirmed)
        self.assertFalse(analysis.has_terminator)  # Verified: Remap L1 has no separate 11th terminator packet

        # Verify Key A in reassembled buffer: offset 200..203 is 00 05 00 02 (B)
        self.assertIsNotNone(analysis.reassembled_buffer)
        slot_50_bytes = analysis.reassembled_buffer[200:204]
        self.assertEqual(slot_50_bytes, bytes([0x00, 0x05, 0x00, 0x02]))



if __name__ == "__main__":
    unittest.main()
