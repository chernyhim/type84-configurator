"""
Unit tests for 64-byte HID output report parser.
"""

import unittest
from keyboard_re.models import (
    CHUNK_PAYLOAD_SIZE,
    CONFIG_IMAGE_SIZE,
    PacketType,
    RawReport,
)
from keyboard_re.parser import parse_capture, parse_single_report
from tests.synthetic_fixtures import (
    build_synthetic_capture,
    create_mock_report_chunk,
    create_mock_terminator_report,
)


class TestParser(unittest.TestCase):

    def test_parse_valid_chunk_packet(self):
        payload = bytes([i % 256 for i in range(CHUNK_PAYLOAD_SIZE)])
        report = create_mock_report_chunk(addr=0x0038, payload_bytes=payload, index=1)
        pkt = parse_single_report(report.as_bytes(), index=1)

        self.assertTrue(pkt.is_valid)
        self.assertEqual(pkt.packet_type, PacketType.DATA_CHUNK)
        self.assertEqual(pkt.header_prefix, bytes([0xAA, 0x27]))
        self.assertEqual(pkt.opcode_or_size, 0x38)
        self.assertEqual(pkt.address, 0x0038)
        self.assertEqual(len(pkt.payload), 56)
        self.assertEqual(pkt.payload, payload)
        self.assertEqual(len(pkt.padding), 3)

    def test_parse_valid_terminator_packet(self):
        report = create_mock_terminator_report(index=18)
        pkt = parse_single_report(report.as_bytes(), index=18)

        self.assertTrue(pkt.is_valid)
        self.assertEqual(pkt.packet_type, PacketType.TERMINATOR)
        self.assertEqual(pkt.header_prefix, bytes([0xAA, 0x27]))
        self.assertEqual(pkt.opcode_or_size, 0x10)
        self.assertEqual(pkt.terminator_size, CONFIG_IMAGE_SIZE)
        self.assertEqual(pkt.terminator_flags, bytes([0x00, 0x01]))

    def test_parse_with_prepended_report_id_0(self):
        payload = bytes([0xAA] * CHUNK_PAYLOAD_SIZE)
        report = create_mock_report_chunk(addr=0x0000, payload_bytes=payload, index=0)
        raw_with_id = bytes([0x00]) + report.as_bytes()
        self.assertEqual(len(raw_with_id), 65)

        pkt = parse_single_report(raw_with_id, index=0)
        self.assertTrue(pkt.is_valid)
        self.assertEqual(pkt.packet_type, PacketType.DATA_CHUNK)
        self.assertEqual(pkt.address, 0x0000)

    def test_parse_invalid_length(self):
        pkt = parse_single_report(bytes([0xAA, 0x27, 0x38] * 10), index=0)
        self.assertFalse(pkt.is_valid)
        self.assertIn("Invalid report size", str(pkt.error_message))

    def test_parse_unaligned_chunk_address(self):
        buf = bytearray(64)
        buf[0:3] = bytes([0xAA, 0x27, 0x38])
        buf[3:5] = bytes([0x05, 0x00])
        pkt = parse_single_report(bytes(buf), index=0)
        self.assertFalse(pkt.is_valid)
        self.assertIn("not aligned", str(pkt.error_message))

    def test_parse_chunk_address_out_of_bounds(self):
        buf = bytearray(64)
        buf[0:3] = bytes([0xAA, 0x27, 0x38])
        buf[3:5] = bytes([0x00, 0x05])
        pkt = parse_single_report(bytes(buf), index=0)
        self.assertFalse(pkt.is_valid)
        self.assertIn("exceeds maximum", str(pkt.error_message))

    def test_parse_full_capture(self):
        capture = build_synthetic_capture()
        parsed = parse_capture(capture)
        self.assertEqual(len(parsed), 19)

        for i in range(18):
            self.assertEqual(parsed[i].packet_type, PacketType.DATA_CHUNK)
            self.assertEqual(parsed[i].address, i * 56)
            self.assertTrue(parsed[i].is_valid)

        self.assertEqual(parsed[18].packet_type, PacketType.TERMINATOR)
        self.assertEqual(parsed[18].terminator_size, 1008)
        self.assertTrue(parsed[18].is_valid)


if __name__ == "__main__":
    unittest.main()
