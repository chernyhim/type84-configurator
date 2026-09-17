"""
Unit tests for MockHidTransport and DryRunTransport.
"""

from __future__ import annotations

import unittest

from keyboard_re.protocol.transport import DryRunTransport, MockHidTransport


class TestTransport(unittest.TestCase):
    def test_mock_transport_auto_ack(self):
        transport = MockHidTransport(auto_ack=True)
        # Send a 64-byte write report
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        pkt[2] = 0x38
        pkt[3] = 0x70
        pkt[4] = 0x01

        transport.send_report(0, pkt)
        self.assertEqual(len(transport.sent_packets), 1)

        ack = transport.receive_report(timeout=1.0)
        self.assertEqual(len(ack), 64)
        self.assertEqual(ack[0], 0x55)
        self.assertEqual(ack[1], 0x27)
        self.assertEqual(ack[2], 0x38)
        self.assertEqual(ack[3], 0x70)
        self.assertEqual(ack[4], 0x01)

    def test_mock_transport_timeout_simulation(self):
        transport = MockHidTransport(simulate_timeout=True)
        pkt = bytes(64)
        transport.send_report(0, pkt)
        with self.assertRaises(TimeoutError):
            transport.receive_report(timeout=0.1)

    def test_mock_transport_wrong_ack_simulation(self):
        transport = MockHidTransport(simulate_wrong_ack_opcode=0x99)
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        transport.send_report(0, pkt)

        ack = transport.receive_report()
        self.assertEqual(ack[1], 0x99)

    def test_dry_run_transport_guarantees(self):
        dry = DryRunTransport()
        self.assertTrue(dry.is_dry_run)
        self.assertEqual(len(dry.recorded_reports), 0)

        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        dry.send_report(0, pkt)
        self.assertEqual(len(dry.recorded_reports), 1)

        ack = dry.receive_report()
        self.assertEqual(ack[0], 0x55)
        self.assertEqual(ack[1], 0x27)

    def test_dry_run_transport_strict_payload_echo(self):
        """
        [CB-3 Regression Test]
        DryRunTransport.receive_report must synthesize an ACK mirroring bytes 5..7
        and the exact chunk payload (bytes 8..8+sz).
        This allows execute_profile_write_plan and validate_chunk_ack with
        strict_payload=True to succeed without failing on zero-payload ACK.
        """
        dry = DryRunTransport()
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x22
        pkt[2] = 56  # sz = 56
        pkt[3:5] = b"\x00\x01"  # addr
        pkt[5:8] = b"\x01\x02\x03"  # subheader flags
        pkt[8:64] = bytes(range(56))  # payload data

        dry.send_report(0, pkt)
        ack = dry.receive_report()

        self.assertEqual(len(ack), 64)
        self.assertEqual(ack[0], 0x55)
        self.assertEqual(ack[1], 0x22)
        self.assertEqual(ack[2], 56)
        self.assertEqual(ack[3:5], b"\x00\x01")
        self.assertEqual(ack[5:8], b"\x01\x02\x03")
        self.assertEqual(ack[8:64], bytes(range(56)))

        # Also test direct validate_chunk_ack with strict_payload=True
        from keyboard_re.protocol.plan import WriteChunk
        from keyboard_re.protocol.executor import validate_chunk_ack

        expected_ack = bytearray(ack)
        chunk = WriteChunk(chunk_index=0, address=0x0100, size=56, packet=bytes(pkt), expected_ack=bytes(expected_ack))
        err = validate_chunk_ack(chunk, ack, strict_payload=True)
        self.assertIsNone(err, f"strict_payload validation failed: {err}")


if __name__ == "__main__":
    unittest.main()
