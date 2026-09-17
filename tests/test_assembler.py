"""
Unit tests for 1008-byte configuration image assembler.
"""

import random
import unittest

from keyboard_re.assembler import assemble_packets
from keyboard_re.models import (
    CHUNK_COUNT,
    CHUNK_PAYLOAD_SIZE,
    CONFIG_IMAGE_SIZE,
    PacketType,
)
from keyboard_re.parser import parse_capture
from tests.synthetic_fixtures import (
    build_synthetic_capture,
    create_mock_report_chunk,
)


class TestAssembler(unittest.TestCase):

    def test_assemble_complete_capture(self):
        capture = build_synthetic_capture()
        parsed = parse_capture(capture)
        res = assemble_packets(parsed)

        self.assertTrue(res.success)
        self.assertIsNotNone(res.image)
        self.assertEqual(len(res.image.data), CONFIG_IMAGE_SIZE)
        self.assertEqual(res.chunks_found, CHUNK_COUNT)
        self.assertTrue(res.has_terminator)
        self.assertEqual(len(res.errors), 0)

    def test_assemble_out_of_order_packets(self):
        capture = build_synthetic_capture()
        parsed = parse_capture(capture)

        shuffled = list(parsed)
        random.seed(42)
        random.shuffle(shuffled)

        res = assemble_packets(shuffled)
        self.assertTrue(res.success)
        self.assertIsNotNone(res.image)
        self.assertEqual(len(res.image.data), 1008)

    def test_assemble_missing_chunk(self):
        capture = build_synthetic_capture()
        parsed = parse_capture(capture)

        filtered = [p for p in parsed if p.address != 0x00A8]
        res = assemble_packets(filtered)

        self.assertFalse(res.success)
        self.assertIsNone(res.image)
        self.assertIn(0x00A8, res.missing_addresses)
        self.assertTrue(any("Missing 1 chunk" in e for e in res.errors))

    def test_assemble_duplicate_conflicting_chunk(self):
        capture = build_synthetic_capture()
        parsed = parse_capture(capture)

        conflict_chunk = create_mock_report_chunk(
            addr=0x0000,
            payload_bytes=bytes([0xFF] * CHUNK_PAYLOAD_SIZE),
            index=99
        )
        parsed.append(parse_capture(build_synthetic_capture())[0])
        from keyboard_re.parser import parse_single_report
        parsed.append(parse_single_report(conflict_chunk.as_bytes(), index=99))

        res = assemble_packets(parsed)
        self.assertFalse(res.success)
        self.assertTrue(any("Conflicting duplicate" in e for e in res.errors))

    def test_payload_placement(self):
        overrides = {
            0: 0x11,
            55: 0x22,
            56: 0x33,
            952: 0x44,
            1007: 0x55
        }
        capture = build_synthetic_capture(custom_overrides=overrides)
        parsed = parse_capture(capture)
        res = assemble_packets(parsed)

        self.assertTrue(res.success)
        img = res.image
        for addr, expected_val in overrides.items():
            self.assertEqual(img.get_byte(addr), expected_val, f"Mismatch at 0x{addr:04X}")


if __name__ == "__main__":
    unittest.main()
