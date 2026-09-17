"""
Unit tests for write_transaction and verify_write engines.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.models.state import KeyboardSnapshot
from keyboard_re.protocol.hall import build_hall_terminator, build_hall_write
from keyboard_re.protocol.transaction import (
    TransactionResult,
    TransactionStatus,
    VerificationResult,
    VerificationStatus,
    verify_write,
    write_transaction,
)
from keyboard_re.protocol.transport import MockHidTransport


class TestTransaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        cls.baseline = KeyboardSnapshot.load_json(capture_path)

    def test_write_transaction_success(self):
        # Generate 19 write packets for Hall profile
        hall_img = self.baseline.active_profile.hall.to_bytes()
        packets = build_hall_write(hall_img) + [build_hall_terminator()]
        self.assertEqual(len(packets), 19)

        transport = MockHidTransport(auto_ack=True)
        res = write_transaction(transport, packets, expected_ack_opcode=0x27)

        self.assertEqual(res.status, TransactionStatus.SUCCESS)
        self.assertEqual(res.packets_sent, 19)
        self.assertEqual(res.acks_received, 19)
        self.assertIsNone(res.error)

    def test_write_transaction_timeout(self):
        transport = MockHidTransport(simulate_timeout=True)
        packets = [bytes(64)]
        res = write_transaction(transport, packets, expected_ack_opcode=0x27)

        self.assertEqual(res.status, TransactionStatus.FAILED)
        self.assertEqual(res.packets_sent, 1)
        self.assertEqual(res.acks_received, 0)
        self.assertIn("Timeout", res.error)

    def test_write_transaction_wrong_ack_opcode(self):
        transport = MockHidTransport(simulate_wrong_ack_opcode=0x17)
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        res = write_transaction(transport, [pkt], expected_ack_opcode=0x27)

        self.assertEqual(res.status, TransactionStatus.FAILED)
        self.assertIn("Invalid ACK opcode", res.error)

    def test_write_transaction_wrong_address(self):
        transport = MockHidTransport(simulate_wrong_ack_address=0x0300)
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        pkt[3] = 0x00
        pkt[4] = 0x00
        res = write_transaction(transport, [pkt], expected_ack_opcode=0x27)

        self.assertEqual(res.status, TransactionStatus.FAILED)
        self.assertIn("Address mismatch", res.error)

    def test_verify_write_success(self):
        intended_state = self.baseline.clone_mutable()
        intended_state.active_profile.hall.set_actuation("A", 1.39)
        intended_snap = intended_state.to_snapshot()

        # Actual after matches intended
        actual_snap = intended_snap

        res = verify_write(
            before=self.baseline,
            intended=intended_snap,
            actual_after=actual_snap,
        )
        self.assertEqual(res.status, VerificationStatus.SUCCESS)
        self.assertEqual(len(res.applied_diffs), 1)
        self.assertEqual(len(res.missing_diffs), 0)
        self.assertEqual(len(res.unexpected_diffs), 0)

    def test_verify_write_failed(self):
        intended_state = self.baseline.clone_mutable()
        intended_state.active_profile.hall.set_actuation("A", 1.39)
        intended_snap = intended_state.to_snapshot()

        # Actual after was not changed (still baseline)
        res = verify_write(
            before=self.baseline,
            intended=intended_snap,
            actual_after=self.baseline,
        )
        self.assertEqual(res.status, VerificationStatus.FAILED)
        self.assertEqual(len(res.applied_diffs), 0)
        self.assertEqual(len(res.missing_diffs), 1)

    def test_verify_write_partial(self):
        intended_state = self.baseline.clone_mutable()
        intended_state.active_profile.hall.set_actuation("A", 1.39)
        intended_state.active_profile.hall.set_actuation("Q", 1.20)
        intended_snap = intended_state.to_snapshot()

        # Actual only applied A, but not Q
        actual_state = self.baseline.clone_mutable()
        actual_state.active_profile.hall.set_actuation("A", 1.39)
        actual_snap = actual_state.to_snapshot()

        res = verify_write(
            before=self.baseline,
            intended=intended_snap,
            actual_after=actual_snap,
        )
        self.assertEqual(res.status, VerificationStatus.PARTIAL)
        self.assertEqual(len(res.applied_diffs), 1)
        self.assertEqual(len(res.missing_diffs), 1)


if __name__ == "__main__":
    unittest.main()
