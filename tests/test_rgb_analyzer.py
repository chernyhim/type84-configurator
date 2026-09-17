"""
Unit tests for the RGB Protocol Analyzer (rgb_analyzer.py).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from keyboard_re.models import RawCapture, RawReport
from keyboard_re.rgb_analyzer import (
    analyze_single_capture,
    compute_crc16,
    detect_checksums_in_report,
    diff_rgb_captures,
    evaluate_led_offset_correlation,
    find_longest_common_prefix,
)


class TestRGBAnalyzer(unittest.TestCase):
    def test_longest_common_prefix(self):
        seqs = [
            bytes.fromhex("AA280100FF0000"),
            bytes.fromhex("AA28010000FF00"),
            bytes.fromhex("AA2801000000FF"),
        ]
        prefix = find_longest_common_prefix(seqs)
        self.assertEqual(prefix.hex().upper(), "AA280100")

    def test_crc16_computation(self):
        data = b"123456789"
        crc = compute_crc16(data, poly=0x1021, init=0xFFFF)
        self.assertIsInstance(crc, int)
        self.assertTrue(0 <= crc <= 0xFFFF)

    def test_checksum_detection_sum_mod_256(self):
        # Payload of 3 bytes: 0x10, 0x20, 0x30 -> sum = 0x60
        pkt = bytes([0xAA, 0x28, 0x01, 0x10, 0x20, 0x30])
        ck = (sum(pkt) & 0xFF)
        full_pkt = pkt + bytes([ck])

        candidates = detect_checksums_in_report(full_pkt)
        matched = [c for c in candidates if c.is_match]
        self.assertTrue(any(c.algorithm == "SUM_MOD_256" for c in matched))

    def test_checksum_detection_xor(self):
        pkt = bytes([0xAA, 0x28, 0x05, 0x55, 0xAA])
        xor_val = 0
        for b in pkt:
            xor_val ^= b
        full_pkt = pkt + bytes([xor_val])

        candidates = detect_checksums_in_report(full_pkt)
        matched = [c for c in candidates if c.is_match]
        self.assertTrue(any(c.algorithm == "XOR_SUM" for c in matched))

    def test_analyze_single_capture_metadata(self):
        reports = [
            RawReport(
                index=0,
                timestamp_ms=10.0,
                report_type="output",
                report_id=0,
                length=64,
                data_hex="AA2801" + "00" * 61,
            )
        ]
        capture = RawCapture(
            version="1.1",
            device={
                "vendor_id": "0x0C45",
                "product_id": "0x80D6",
                "product_name": "Type 84 Test",
                "collections": [
                    {
                        "usage_page": "0xFFA0",
                        "usage": "0x0001",
                        "output_report_ids": [0],
                    }
                ]
            },
            captured_at="2026-09-14T12:00:00Z",
            description="Test RGB Single Capture",
            reports_count=1,
            reports=reports,
        )

        analysis = analyze_single_capture(capture, filename="test_rgb.json")
        self.assertEqual(analysis.reports_count, 1)
        self.assertEqual(analysis.unique_report_ids, [0])
        self.assertEqual(analysis.unique_lengths, [64])
        self.assertEqual(analysis.opcodes, ["AA28"])
        self.assertFalse(analysis.is_aa27_protocol)
        self.assertEqual(analysis.device_info.usage_page, "0xFFA0")
        self.assertEqual(analysis.device_info.usage, "0x0001")

    def test_diff_rgb_color_change(self):
        base_rep = RawReport(
            index=0,
            timestamp_ms=10.0,
            report_type="output",
            report_id=0,
            length=64,
            data_hex="AA2801FF0000" + "00" * 58,  # Red
        )
        exp_rep = RawReport(
            index=0,
            timestamp_ms=20.0,
            report_type="output",
            report_id=0,
            length=64,
            data_hex="AA28010000FF" + "00" * 58,  # Blue
        )
        base_cap = RawCapture(
            version="1.1",
            device={},
            captured_at="2026-09-14T12:00:00Z",
            description="Base Red",
            reports_count=1,
            reports=[base_rep],
        )
        exp_cap = RawCapture(
            version="1.1",
            device={},
            captured_at="2026-09-14T12:01:00Z",
            description="Exp Blue",
            reports_count=1,
            reports=[exp_rep],
        )

        diff = diff_rgb_captures(base_cap, exp_cap, action_type="color")
        self.assertTrue(diff.is_separate_from_aa27)
        self.assertEqual(len(diff.byte_diffs), 2)  # R changed (FF -> 00), B changed (00 -> FF)
        self.assertIn(3, diff.changed_offsets)  # byte 3: R
        self.assertIn(5, diff.changed_offsets)  # byte 5: B
        self.assertEqual(diff.invariant_prefix_hex, "AA2801")

    def test_diff_rgb_brightness_change(self):
        base_rep = RawReport(
            index=0,
            timestamp_ms=10.0,
            report_type="output",
            report_id=0,
            length=64,
            data_hex="AA280264" + "00" * 60,  # 0x64 = 100%
        )
        exp_rep = RawReport(
            index=0,
            timestamp_ms=20.0,
            report_type="output",
            report_id=0,
            length=64,
            data_hex="AA280232" + "00" * 60,  # 0x32 = 50%
        )
        base_cap = RawCapture(
            version="1.1",
            device={},
            captured_at="",
            description="",
            reports_count=1,
            reports=[base_rep],
        )
        exp_cap = RawCapture(
            version="1.1",
            device={},
            captured_at="",
            description="",
            reports_count=1,
            reports=[exp_rep],
        )

        diff = diff_rgb_captures(base_cap, exp_cap, action_type="brightness")
        self.assertEqual(len(diff.byte_diffs), 1)
        self.assertEqual(diff.changed_offsets, [3])
        d = diff.byte_diffs[0]
        self.assertEqual(d.old_value, 100)
        self.assertEqual(d.new_value, 50)
        self.assertEqual(d.delta, -50)

    def test_evaluate_led_offset_correlation(self):
        # Header of 4 bytes, LED 10 at offset 4 + 10*3 = 34
        res = evaluate_led_offset_correlation(34)
        self.assertEqual(res.probable_led_index, 10)
        self.assertEqual(res.correlation_type, "PHYSICAL_LINEAR_RGB_TRIPLET")


class TestRealRGBCaptures(unittest.TestCase):
    """Integration tests verifying the 5 real WebHID RGB captures."""

    @classmethod
    def setUpClass(cls):
        cls.exp_dir = Path("captures/experiments")
        cls.f_color = cls.exp_dir / "rgb_01_color.json"
        cls.f_bright = cls.exp_dir / "rgb_02_brightness.json"
        cls.f_effect = cls.exp_dir / "rgb_03_effect.json"
        cls.f_speed = cls.exp_dir / "rgb_04_speed.json"
        cls.f_per_key = cls.exp_dir / "rgb_05_per_key.json"

    def test_real_rgb_01_color(self):
        if not self.f_color.exists():
            self.skipTest("rgb_01_color.json not found")
        cap = RawCapture.load_json(self.f_color)
        analysis = analyze_single_capture(cap, "rgb_01_color.json")
        self.assertEqual(analysis.device_info.usage_page, "0xFF68")
        self.assertEqual(analysis.device_info.usage, "0x0061")
        self.assertEqual(analysis.unique_report_ids, [0])
        self.assertEqual(analysis.unique_lengths, [64])
        self.assertEqual(analysis.opcodes, ["AA23"])
        self.assertFalse(analysis.is_aa27_protocol)

        # Check fields of the 24-byte payload
        b = bytes.fromhex(cap.reports[0].data_hex)
        self.assertEqual(b[:3].hex().upper(), "AA2310")
        self.assertEqual(b[8], 1, "Mode 1 = Static")
        self.assertEqual(b[9:12], b"\xFF\x00\x00", "Red color #FF0000")
        self.assertEqual(b[17], 5, "Brightness = 5 (100%)")
        self.assertEqual(b[18], 3, "Speed = 3")
        self.assertEqual(b[22:24], b"\xAA\x55", "Magic signature 0xAA55")

    def test_real_rgb_02_brightness(self):
        if not (self.f_color.exists() and self.f_bright.exists()):
            self.skipTest("captures not found")
        cap_bright = RawCapture.load_json(self.f_bright)
        b = bytes.fromhex(cap_bright.reports[1].data_hex)
        self.assertEqual(b[17], 3, "Brightness decreased from 5 to 3 (50%)")

        diff = diff_rgb_captures(
            RawCapture.load_json(self.f_color),
            cap_bright,
            action_type="brightness"
        )
        self.assertIn(17, diff.changed_offsets)
        d = next(d for d in diff.byte_diffs if d.byte_offset == 17)
        self.assertEqual(d.old_value, 5)
        self.assertEqual(d.new_value, 3)

    def test_real_rgb_03_effect(self):
        if not (self.f_bright.exists() and self.f_effect.exists()):
            self.skipTest("captures not found")
        cap_eff = RawCapture.load_json(self.f_effect)
        b = bytes.fromhex(cap_eff.reports[0].data_hex)
        self.assertEqual(b[8], 7, "Mode 7 = Breathing")

    def test_real_rgb_04_speed(self):
        if not (self.f_effect.exists() and self.f_speed.exists()):
            self.skipTest("captures not found")
        cap_speed = RawCapture.load_json(self.f_speed)
        b = bytes.fromhex(cap_speed.reports[0].data_hex)
        self.assertEqual(b[18], 5, "Speed increased from 3 to 5 (max)")

        diff = diff_rgb_captures(
            RawCapture.load_json(self.f_effect),
            cap_speed,
            action_type="speed"
        )
        self.assertEqual(diff.changed_offsets, [18])
        self.assertEqual(diff.byte_diffs[0].old_value, 3)
        self.assertEqual(diff.byte_diffs[0].new_value, 5)

    def test_real_rgb_05_per_key(self):
        if not self.f_per_key.exists():
            self.skipTest("rgb_05_per_key.json not found")
        cap = RawCapture.load_json(self.f_per_key)
        self.assertEqual(len(cap.reports), 41)

        # Report 0 activates custom mode 0x80
        r0 = bytes.fromhex(cap.reports[0].data_hex)
        self.assertEqual(r0[8], 0x80, "Mode 0x80 = Custom / Per-Key")

        # Reconstruct block 0 (burst 0: W is Red) and block 1 (burst 1: W is Green)
        def reassemble_512(reps):
            buf = bytearray()
            for r in reps:
                b = bytes.fromhex(r.data_hex)
                plen = b[2]
                buf.extend(b[5:5+plen])
            return buf

        b0 = reassemble_512(cap.reports[1:11])
        b1 = reassemble_512(cap.reports[11:21])
        self.assertEqual(len(b0), 512)
        self.assertEqual(len(b1), 512)

        # Record 35 corresponds to Key W
        rec35_b0 = b0[35*4 : 36*4]
        rec35_b1 = b1[35*4 : 36*4]
        self.assertEqual(rec35_b0, bytes([255, 0, 0, 35]), "W was initially Red in slot 35")
        self.assertEqual(rec35_b1, bytes([0, 255, 0, 35]), "W became Green in slot 35")

        # Entire 512 bytes are identical except for record 35's R and G
        diff_indices = [i for i in range(512) if b0[i] != b1[i]]
        self.assertEqual(diff_indices, [140, 141], "Only bytes 140 (Red) and 141 (Green) changed")


if __name__ == "__main__":
    unittest.main()

