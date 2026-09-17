"""
Unit tests for safe ProfileWritePlan Executor.

Verifies:
1. Pure mock / dry-run execution (ZERO physical writes).
2. Canonical step ordering and chunk sequencing.
3. Strict ACK validation: prefix 0x55, opcode, size, address, payload echo.
4. Fail-fast semantics: immediate abort on wrong prefix, wrong opcode, wrong address,
   wrong size, payload echo mismatch, transport timeout, and transport send errors.
5. Verification that NO further packets are sent after an abort.
6. Post-write closed-loop readback verification against target Profile.
7. Readback mismatch detection and detailed ProfileDiff reporting.
8. Readback transport error handling.
9. Empty plan early-out.
"""

from __future__ import annotations

import copy
from pathlib import Path
import struct
import unittest

from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.executor import (
    ChunkExecutionResult,
    ExecutionStatus,
    PlanExecutionResult,
    ProfilePlanExecutor,
    StepExecutionResult,
    execute_profile_write_plan,
    validate_chunk_ack,
)
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.plan import (
    ProfileWritePlan,
    SubsystemWriteStep,
    WriteChunk,
    build_profile_write_plan,
)
from keyboard_re.protocol.transport import MockHidTransport


class TestProfilePlanExecutor(unittest.TestCase):
    """Unit test suite for safe ProfileWritePlan execution."""

    @classmethod
    def setUpClass(cls):
        capture_path = (
            Path(__file__).parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.base_state = DeviceState.load_json(capture_path)
        cls.manager = ProfileManager()

    def setUp(self):
        self.baseline = self.base_state.clone()
        self.transport = MockHidTransport()

    def test_empty_plan_execution(self):
        """Empty plan executes immediately with 0 transmissions and success status."""
        plan = ProfileWritePlan(profile_id=1, profile_name="Empty Plan")
        profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Empty")

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=profile,
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.executed_steps, 0)
        self.assertEqual(result.packets_sent, 0)
        self.assertEqual(result.acks_received, 0)
        self.assertTrue(result.readback_verified)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_successful_single_step_remap_l1(self):
        """Execute a 10-chunk Remap L1 step and verify post-write readback."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Modified L1")
        # Change slot 1 (Esc) to CapsLock (scancode 57)
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)

        plan = build_profile_write_plan(self.baseline, target_profile)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].subsystem, "remap_l1")
        self.assertEqual(plan.total_packets, 10)

        # Mock readback function returns the state with modified L1
        mock_applied_state = self.baseline.clone()
        mock_applied_state.remap_l1.set_slot(1, new_rec)

        def mock_readback(tr, timeout):
            return mock_applied_state

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            readback_func=mock_readback,
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.executed_steps, 1)
        self.assertEqual(result.packets_sent, 10)
        self.assertEqual(result.acks_received, 10)
        self.assertTrue(result.readback_verified)
        self.assertIsNone(result.error)
        self.assertEqual(len(self.transport.recorded_reports), 10)

        # Verify chunk 0 was AA 22 38 00 00
        first_report = self.transport.recorded_reports[0][1]
        self.assertEqual(first_report[:5], bytes.fromhex("AA 22 38 00 00"))

        # Verify tail chunk 9 was AA 22 08 F8 01 (address 504)
        tail_report = self.transport.recorded_reports[9][1]
        self.assertEqual(tail_report[:5], bytes.fromhex("AA 22 08 F8 01"))

    def test_successful_single_step_remap_l2(self):
        """Remap L2 modification produces exactly 1 step (10 chunks of AA 26) and verifies readback."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="L2 Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=0x2C, special=0, function_type=0x02)
        target_profile.remap_l2.set_slot(34, new_rec)

        plan = build_profile_write_plan(self.baseline, target_profile)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].subsystem, "remap_l2")
        self.assertEqual(plan.steps[0].opcode, 0x26)
        self.assertEqual(plan.steps[0].packet_count, 10)

        # Mock readback function returns the state with modified L2
        mock_applied_state = self.baseline.clone()
        mock_applied_state.remap_l2.set_slot(34, new_rec)

        def mock_readback(tr, timeout):
            return mock_applied_state

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            readback_func=mock_readback,
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.executed_steps, 1)
        self.assertEqual(result.packets_sent, 10)
        self.assertEqual(result.acks_received, 10)
        self.assertTrue(result.readback_verified)
        self.assertIsNone(result.error)
        self.assertEqual(len(self.transport.recorded_reports), 10)

        # Verify chunk 0 was AA 26 38 00 00
        first_report = self.transport.recorded_reports[0][1]
        self.assertEqual(first_report[:5], bytes.fromhex("AA 26 38 00 00"))

        # Verify tail chunk 9 was AA 26 08 F8 01 (address 504)
        tail_report = self.transport.recorded_reports[9][1]
        self.assertEqual(tail_report[:5], bytes.fromhex("AA 26 08 F8 01"))

    def test_canonical_step_order_multi_subsystem(self):
        """Plan with Remap L1, RGB Global, and Hall RT executes strictly in canonical order."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Multi Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        target_profile.rgb_global.effect = 2             # Breathing
        target_profile.hall.set_actuation("A", 1.50)     # Hall change

        plan = build_profile_write_plan(self.baseline, target_profile)
        self.assertEqual(len(plan.steps), 3)
        self.assertEqual(plan.steps[0].subsystem, "remap_l1")    # 0x22 (10 chunks)
        self.assertEqual(plan.steps[1].subsystem, "rgb_global")  # 0x23 (1 chunk)
        self.assertEqual(plan.steps[2].subsystem, "hall")        # 0x27 (19 chunks)

        mock_state = self.baseline.clone()
        mock_state.remap_l1.set_slot(1, new_rec)
        mock_state.rgb_global.effect = 2
        mock_state.hall_profile_1.set_actuation("A", 1.50)

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            readback_func=lambda tr, to: mock_state,
        )

        self.assertTrue(result.is_success)
        self.assertEqual(result.executed_steps, 3)
        self.assertEqual(result.packets_sent, 30)  # 10 + 1 + 19
        self.assertEqual(result.acks_received, 30)

        # Check recorded opcodes in transmission sequence
        opcodes = [r[1][1] for r in self.transport.recorded_reports]
        # First 10 are 0x22
        self.assertEqual(opcodes[:10], [0x22] * 10)
        # 11th is 0x23
        self.assertEqual(opcodes[10], 0x23)
        # Remaining 19 are 0x27
        self.assertEqual(opcodes[11:], [0x27] * 19)

    def test_fail_fast_on_wrong_ack_prefix(self):
        """Aborts immediately if device returns ACK with prefix != 0x55."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        # Queue a bad ACK for chunk 0 (prefix 0xEE instead of 0x55)
        bad_ack = bytearray(64)
        bad_ack[0] = 0xEE
        bad_ack[1] = 0x22
        bad_ack[2] = 56
        self.transport.auto_ack = False
        self.transport.queue_response(bytes(bad_ack))

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.is_success)
        self.assertEqual(result.failed_step_index, 0)
        self.assertEqual(result.failed_chunk_index, 0)
        self.assertIn("Invalid ACK prefix 0xEE", result.error)
        self.assertFalse(result.readback_verified)
        # FAIL-FAST: Only 1 packet was sent; subsequent 9 packets were aborted!
        self.assertEqual(result.packets_sent, 1)
        self.assertEqual(len(self.transport.recorded_reports), 1)

    def test_fail_fast_on_wrong_ack_opcode(self):
        """Aborts immediately if device returns wrong ACK opcode."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        self.transport.simulate_wrong_ack_opcode = 0x99

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("Invalid ACK opcode 0x99", result.error)
        self.assertEqual(result.packets_sent, 1)
        self.assertEqual(len(self.transport.recorded_reports), 1)

    def test_fail_fast_on_wrong_ack_address(self):
        """Aborts immediately if device returns wrong address in ACK."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        self.transport.simulate_wrong_ack_address = 0x1234

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("ACK address mismatch", result.error)
        self.assertEqual(result.packets_sent, 1)

    def test_fail_fast_on_wrong_ack_size(self):
        """Aborts immediately if device returns unexpected payload size in ACK."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        bad_ack = bytearray(64)
        bad_ack[0] = 0x55
        bad_ack[1] = 0x22
        bad_ack[2] = 16  # Expected 56!
        self.transport.auto_ack = False
        self.transport.queue_response(bytes(bad_ack))

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("ACK payload size mismatch", result.error)
        self.assertEqual(result.packets_sent, 1)

    def test_fail_fast_on_payload_echo_mismatch(self):
        """Aborts immediately if device ACK does not echo packet payload."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        bad_ack = bytearray(64)
        bad_ack[0] = 0x55
        bad_ack[1] = 0x22
        bad_ack[2] = 56
        struct.pack_into("<H", bad_ack, 3, 0)
        # Corrupt byte 8 of payload (canonical payload start offset)
        bad_ack[8] = 0xFF

        self.transport.auto_ack = False
        self.transport.queue_response(bytes(bad_ack))

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            strict_payload=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("ACK payload echo mismatch", result.error)
        self.assertEqual(result.packets_sent, 1)

    def test_fail_fast_on_transport_timeout(self):
        """Aborts immediately on transport receive timeout."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        self.transport.simulate_timeout = True

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("timeout", result.error.lower())
        self.assertEqual(result.packets_sent, 1)
        self.assertEqual(result.acks_received, 0)

    def test_fail_fast_on_transport_send_error(self):
        """Aborts immediately on transport send exception."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        self.transport.fail_next_write = True

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("send error", result.error.lower())
        self.assertEqual(result.packets_sent, 0)

    def test_fail_fast_stops_between_steps(self):
        """If step 1 fails on chunk 2, step 2 is never attempted."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        target_profile.rgb_global.effect = 3
        plan = build_profile_write_plan(self.baseline, target_profile)
        self.assertEqual(len(plan.steps), 2)  # remap_l1, rgb_global

        # Allow chunk 0 to succeed, then fail chunk 1 with bad opcode
        chunk0_ack = bytearray(plan.steps[0].chunks[0].expected_ack)
        chunk1_bad = bytearray(plan.steps[0].chunks[1].expected_ack)
        chunk1_bad[1] = 0xFE

        self.transport.auto_ack = False
        self.transport.queue_response(bytes(chunk0_ack))
        self.transport.queue_response(bytes(chunk1_bad))

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.failed_step_index, 0)
        self.assertEqual(result.failed_chunk_index, 1)
        self.assertEqual(result.packets_sent, 2)
        # Step 2 (RGB Global) was NEVER executed
        self.assertEqual(len(result.step_results), 1)

    def test_readback_mismatch_fails_execution(self):
        """If write packets succeed but readback reveals mismatch, execution fails with diff."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        # Mock readback returns UNCHANGED baseline (firmware ignored write)
        def mock_readback_unchanged(tr, timeout):
            return self.baseline.clone()

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            readback_func=mock_readback_unchanged,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.readback_verified)
        self.assertIn("Readback verification mismatch", result.error)
        self.assertIsNotNone(result.readback_diff)
        self.assertTrue(result.readback_diff.has_changes)
        self.assertIn("remap_l1", result.readback_diff.changed_subsystems)

    def test_readback_transport_error_reported(self):
        """If readback raises an exception, execution fails with readback error."""
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        def mock_readback_crash(tr, timeout):
            raise ConnectionError("Device disconnected during readback")

        result = execute_profile_write_plan(
            transport=self.transport,
            plan=plan,
            target_profile=target_profile,
            readback_func=mock_readback_crash,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.readback_verified)
        self.assertIn("Device disconnected during readback", result.error)

    def test_format_text_output(self):
        """Verify format_text produces clean audit text for both success and failure."""
        plan = ProfileWritePlan(profile_id=1, profile_name="Audit Profile")
        profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Audit Profile")

        res_ok = PlanExecutionResult(
            status=ExecutionStatus.SUCCESS,
            profile_id=1,
            profile_name="Audit Profile",
            total_steps=1,
            executed_steps=1,
            total_packets=10,
            packets_sent=10,
            acks_received=10,
            readback_verified=True,
        )
        text_ok = res_ok.format_text()
        self.assertIn("PROFILE EXECUTION RESULT: SUCCESS", text_ok)
        self.assertIn("Readback Verified: YES", text_ok)

        res_fail = PlanExecutionResult(
            status=ExecutionStatus.FAILED,
            profile_id=1,
            profile_name="Audit Profile",
            total_steps=2,
            executed_steps=0,
            total_packets=20,
            packets_sent=1,
            acks_received=0,
            failed_step_index=0,
            failed_chunk_index=0,
            error="Simulated transport timeout",
        )
        text_fail = res_fail.format_text()
        self.assertIn("PROFILE EXECUTION RESULT: FAILED", text_fail)
        self.assertIn("Simulated transport timeout", text_fail)

    def test_validate_chunk_ack_payload_offset_canonical(self):
        """
        [CB-1 Regression Test]
        Strict payload echo validation must check canonical offset 8 (bytes 8..8+sz),
        NOT offset 5 (bytes 5..5+sz).

        Proves:
        1. When bytes 5..7 differ (e.g. subheader flags differences) but payload at bytes 8..8+sz
           is identical, canonical offset 8 succeeds (returns None), whereas an offset-5 check
           fails with 'ACK payload echo mismatch'.
        2. When payload at tail (byte 8+sz-1) differs, canonical offset 8 detects mismatch,
           whereas an offset-5 check stops 3 bytes too early and completely misses it.
        """
        sz = 56
        addr = 0x0100
        packet = bytearray(64)
        packet[0:3] = b"\xAA\x22\x38"
        struct.pack_into("<H", packet, 3, addr)
        packet[5:8] = b"\x00\x01\x00"  # subheader flags
        packet[8:8 + sz] = bytes(range(1, sz + 1))  # payload 1..56

        expected_ack = bytearray(64)
        expected_ack[0:3] = b"\x55\x22\x38"
        struct.pack_into("<H", expected_ack, 3, addr)
        expected_ack[5:8] = b"\x00\x01\x00"  # flags
        expected_ack[8:8 + sz] = bytes(range(1, sz + 1))  # identical payload

        chunk = WriteChunk(chunk_index=0, address=addr, size=sz, packet=bytes(packet), expected_ack=bytes(expected_ack))

        # Case 1: subheader flags at bytes 5..7 differ, but payload at 8..8+sz is 100% exact:
        ack_diff_subheader = bytearray(expected_ack)
        ack_diff_subheader[5:8] = b"\x00\x00\x00"  # different subheader
        # Canonical offset 8 MUST pass:
        err = validate_chunk_ack(chunk, bytes(ack_diff_subheader), strict_payload=True)
        self.assertIsNone(err)

        # But if evaluated with offset 5 (the buggy behavior), it would fail:
        buggy_offset_5_expected = expected_ack[5 : 5 + sz]
        buggy_offset_5_actual = ack_diff_subheader[5 : 5 + sz]
        self.assertNotEqual(buggy_offset_5_expected, buggy_offset_5_actual)

        # Case 2: subheader matches, but tail byte of payload (byte 8 + sz - 1 = byte 63) is corrupted:
        ack_corrupt_tail = bytearray(expected_ack)
        ack_corrupt_tail[8 + sz - 1] ^= 0xFF  # corrupt byte 63
        # Canonical offset 8 MUST catch the corruption:
        err = validate_chunk_ack(chunk, bytes(ack_corrupt_tail), strict_payload=True)
        self.assertIsNotNone(err)
        self.assertIn("ACK payload echo mismatch", err)

        # But with offset 5, bytes [5:61] were checked, so byte 63 was skipped!
        self.assertEqual(expected_ack[5 : 5 + sz], ack_corrupt_tail[5 : 5 + sz])

    def test_dry_run_transport_strict_payload_execution(self):
        """
        [CB-3 Regression Test]
        DryRunTransport must synthesize ACKs with full matching payload so that
        execute_profile_write_plan with strict_payload=True succeeds without error.
        """
        from keyboard_re.protocol.transport import DryRunTransport

        dry_transport = DryRunTransport()
        target_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Target")
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        target_profile.remap.set_slot(1, new_rec)
        plan = build_profile_write_plan(self.baseline, target_profile)

        # Execute using DryRunTransport with strict_payload=True
        mock_state = self.baseline.clone()
        mock_state.remap_l1.set_slot(1, new_rec)

        result = execute_profile_write_plan(
            transport=dry_transport,
            plan=plan,
            target_profile=target_profile,
            strict_payload=True,
            readback_func=lambda tr, to: mock_state,
        )

        self.assertTrue(result.is_success)
        self.assertEqual(result.packets_sent, 10)
        self.assertEqual(result.acks_received, 10)
        self.assertTrue(result.readback_verified)


if __name__ == "__main__":
    unittest.main()
