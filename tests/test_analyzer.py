"""
Unit tests for experimental analyzer.
"""

import unittest

from keyboard_re.analyzer import ExperimentalAnalyzer
from keyboard_re.assembler import assemble_packets
from keyboard_re.models import ConfidenceLevel
from keyboard_re.parser import parse_capture
from tests.synthetic_fixtures import build_synthetic_capture


class TestAnalyzer(unittest.TestCase):

    def setUp(self):
        base_capture = build_synthetic_capture()
        self.base_image = assemble_packets(parse_capture(base_capture)).image

    def test_record_experiment_key_s(self):
        mod_capture = build_synthetic_capture(
            custom_overrides={0x0192: 0x8B},
            description="Change Key S actuation 1.40 -> 1.39mm"
        )
        mod_image = assemble_packets(parse_capture(mod_capture)).image

        analyzer = ExperimentalAnalyzer()
        diffs = analyzer.record_experiment(
            base_img=self.base_image,
            mod_img=mod_image,
            action_description="Key S actuation 1.40 -> 1.39mm",
            target_key="S",
            parameter_name="Actuation Point"
        )

        self.assertEqual(len(diffs), 1)
        slot50 = analyzer.slots[50]
        byte2 = slot50.bytes_info[2]

        self.assertEqual(byte2.key_name, "S")
        self.assertEqual(byte2.field_name, "Actuation Point")
        self.assertEqual(byte2.confidence, ConfidenceLevel.CONFIRMED)
        self.assertEqual(len(byte2.change_events), 1)

        # Ensure untested bytes in slot 50 remain UNKNOWN
        for off in (0, 1, 3, 5, 7):
            self.assertEqual(
                slot50.bytes_info[off].confidence,
                ConfidenceLevel.UNKNOWN,
                f"Offset +{off} must remain UNKNOWN without experimental evidence"
            )
        self.assertEqual(slot50.bytes_info[4].confidence, ConfidenceLevel.CONFIRMED)
        self.assertEqual(slot50.bytes_info[4].field_name, "RT Press Sensitivity")
        self.assertEqual(slot50.bytes_info[6].confidence, ConfidenceLevel.CONFIRMED)
        self.assertEqual(slot50.bytes_info[6].field_name, "RT Release Sensitivity")

    def test_slot_report_formatting(self):
        analyzer = ExperimentalAnalyzer()
        report_text = analyzer.format_slot_report(50)
        self.assertIn("Slot 050", report_text)
        self.assertIn("Actuation Point", report_text)
        self.assertIn("[CONFIRMED]", report_text)


if __name__ == "__main__":
    unittest.main()
