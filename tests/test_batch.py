"""
Unit tests for batch analyzer and strict confidence escalation rules.
"""

import json
import unittest
from pathlib import Path

from keyboard_re.batch import BatchAnalyzer, ExperimentSpec
from keyboard_re.models import ConfidenceLevel
from tests.synthetic_fixtures import build_synthetic_capture


class TestBatchAnalyzer(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path("captures/samples")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Baseline
        self.base_cap = build_synthetic_capture(description="Base 1.40mm")
        self.base_path = self.temp_dir / "test_base.json"
        self.base_cap.save_json(self.base_path)

        # Key A mod (slot 49, off 5: 0x018D)
        self.cap_a = build_synthetic_capture(custom_overrides={0x018D: 0x8B}, description="Key A 1.39mm")
        self.a_path = self.temp_dir / "test_exp_a.json"
        self.cap_a.save_json(self.a_path)

        # Key S mod (slot 50, off 5: 0x0195)
        self.cap_s = build_synthetic_capture(custom_overrides={0x0195: 0x8B}, description="Key S 1.39mm")
        self.s_path = self.temp_dir / "test_exp_s.json"
        self.cap_s.save_json(self.s_path)

        # Key A second mod with different value (slot 49, off 5: 0x018D = 0x80)
        self.cap_a2 = build_synthetic_capture(custom_overrides={0x018D: 0x80}, description="Key A 1.28mm")
        self.a2_path = self.temp_dir / "test_exp_a2.json"
        self.cap_a2.save_json(self.a2_path)

        # Noisy mod touching 2 bytes simultaneously
        self.cap_noisy = build_synthetic_capture(
            custom_overrides={0x018D: 0x8B, 0x018E: 0x01},
            description="Noisy multiple bytes"
        )
        self.noisy_path = self.temp_dir / "test_exp_noisy.json"
        self.cap_noisy.save_json(self.noisy_path)

    def test_single_experiment_never_auto_confirms(self):
        """
        Rule: "Не делать выводов о назначении неизвестных байтов автоматически только на основании одного эксперимента."
        A single experiment must result in PROBABLE, never CONFIRMED.
        """
        analyzer = BatchAnalyzer()
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_single",
                base_capture=str(self.base_path),
                experimental_capture=str(self.a_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )

        outcomes = analyzer.run_batch()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "ISOLATED_OK")
        self.assertEqual(outcomes[0].detected_slot, 49)
        self.assertEqual(outcomes[0].detected_offset, 5)

        # Key mapping on single test must be PROBABLE, not CONFIRMED
        k_corr = analyzer.key_correlations["A"]
        self.assertEqual(k_corr.confidence, ConfidenceLevel.PROBABLE)

        # Offset mapping on single test must be PROBABLE, not CONFIRMED
        o_corr = analyzer.offset_correlations[5]
        self.assertEqual(o_corr.confidence, ConfidenceLevel.PROBABLE)

        # All untested offsets (0..4, 6..7) must remain UNKNOWN
        for off in (0, 1, 2, 3, 4, 6, 7):
            self.assertNotIn(off, analyzer.offset_correlations)

    def test_cross_validation_across_keys_confirms_offset(self):
        """
        When >= 2 independent experiments across different keys (A and S)
        both map to offset +5 for 'Actuation Point', the offset mapping becomes CONFIRMED.
        """
        analyzer = BatchAnalyzer()
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_a",
                base_capture=str(self.base_path),
                experimental_capture=str(self.a_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_s",
                base_capture=str(self.base_path),
                experimental_capture=str(self.s_path),
                key_name="S",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )

        analyzer.run_batch()

        # Offset +5 was validated across 2 distinct keys (A and S)
        o_corr = analyzer.offset_correlations[5]
        self.assertEqual(o_corr.confidence, ConfidenceLevel.CONFIRMED)
        self.assertIn("A", o_corr.keys_tested)
        self.assertIn("S", o_corr.keys_tested)

    def test_repeated_experiments_on_same_key_confirms_key_slot(self):
        """
        When >= 2 independent experiments on the SAME key (A) target the same slot,
        the Key->Slot correlation advances to CONFIRMED.
        """
        analyzer = BatchAnalyzer()
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_a1",
                base_capture=str(self.base_path),
                experimental_capture=str(self.a_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_a2",
                base_capture=str(self.base_path),
                experimental_capture=str(self.a2_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.28mm"
            )
        )

        analyzer.run_batch()

        k_corr = analyzer.key_correlations["A"]
        self.assertEqual(k_corr.confidence, ConfidenceLevel.CONFIRMED)
        self.assertEqual(k_corr.slot_index, 49)
        self.assertEqual(k_corr.observations_count, 2)

    def test_noisy_experiment_does_not_deduce_mappings(self):
        """
        When an experiment modifies multiple bytes simultaneously,
        it must be classified as NOISY and not pollute confirmed mappings.
        """
        analyzer = BatchAnalyzer()
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_noisy",
                base_capture=str(self.base_path),
                experimental_capture=str(self.noisy_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )

        outcomes = analyzer.run_batch()
        self.assertEqual(outcomes[0].status, "NOISY")

        # No confirmed or probable mappings should be established from noise
        self.assertEqual(len(analyzer.key_correlations), 0)
        self.assertEqual(len(analyzer.offset_correlations), 0)

    def test_terminal_and_markdown_output(self):
        analyzer = BatchAnalyzer()
        analyzer.add_experiment(
            ExperimentSpec(
                id="exp_a",
                base_capture=str(self.base_path),
                experimental_capture=str(self.a_path),
                key_name="A",
                parameter_name="Actuation Point",
                old_value="1.40mm",
                new_value="1.39mm"
            )
        )
        analyzer.run_batch()

        text_tables = analyzer.format_terminal_tables()
        self.assertIn("exp_a", text_tables)
        self.assertIn("S049 : +5", text_tables)
        self.assertIn("0x018D", text_tables)

        md_report = analyzer.generate_markdown_report()
        self.assertIn("| **A** | `S049` |", md_report)
        self.assertIn("`+5`", md_report)

        as_dict = analyzer.to_dict()
        self.assertEqual(as_dict["total_experiments"], 1)
        self.assertEqual(as_dict["correlations"][0]["key"], "A")
        self.assertEqual(as_dict["correlations"][0]["slot_index"], 49)


if __name__ == "__main__":
    unittest.main()
