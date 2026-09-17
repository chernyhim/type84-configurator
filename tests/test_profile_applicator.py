"""
Unit tests for high-level apply_profile facade and ProfileApplicator.

Verifies:
1. Pure mock / dry-run testing (ZERO physical transmissions).
2. No-op detection: identical profile produces status=NO_CHANGES and zero writes.
3. Dry-run preview: plan and summary generated without touching write transport.
4. Security gates: missing or rejecting confirmation callback strictly aborts with zero writes.
5. Confirmation callback receives complete PlanConfirmationSummary (subsystems, packets, opcodes, warning).
6. Execution delegation: confirmed application executes through ProfilePlanExecutor.
7. Error propagation: transmission failures and readback mismatches are faithfully reported.
8. Read failure handling: transport read exceptions return structured FAILED result.
9. ProfileApplicator service and ProfileManager.apply_profile() delegation.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from keyboard_re.applicator import (
    ApplyProfileResult,
    PlanConfirmationSummary,
    ProfileApplicator,
    apply_profile,
)
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.executor import ExecutionStatus
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.transport import MockHidTransport


class TestProfileApplicatorFacade(unittest.TestCase):
    """Unit test suite for apply_profile facade."""

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

    def test_noop_identical_profile(self):
        """If profile matches device state, returns NO_CHANGES with 0 writes and no confirmation."""
        identical_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Identical"
        )
        callback_called = False

        def callback(summary: PlanConfirmationSummary) -> bool:
            nonlocal callback_called
            callback_called = True
            return True

        result = apply_profile(
            transport=self.transport,
            profile=identical_profile,
            current_state=self.baseline,
            confirm_callback=callback,
        )

        self.assertEqual(result.status, ExecutionStatus.NO_CHANGES)
        self.assertTrue(result.is_no_changes)
        self.assertTrue(result.is_success)
        self.assertFalse(callback_called)
        self.assertIsNone(result.execution_result)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_dry_run_preview_mode(self):
        """In dry-run mode, plan and summary are built but 0 writes occur and confirmation is bypassed."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        # Remap slot 1 (Esc) to CapsLock (scancode 57)
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        callback_called = False

        def callback(summary: PlanConfirmationSummary) -> bool:
            nonlocal callback_called
            callback_called = True
            return True

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            dry_run=True,
            confirm_callback=callback,
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.dry_run)
        self.assertTrue(result.is_success)
        self.assertFalse(callback_called)
        self.assertIsNotNone(result.plan)
        self.assertEqual(len(result.plan.steps), 1)
        self.assertEqual(result.plan.total_packets, 10)
        self.assertIsNotNone(result.confirmation_summary)
        self.assertEqual(result.confirmation_summary.total_packets, 10)
        self.assertIn("remap_l1", result.confirmation_summary.changed_subsystems)
        self.assertIsNone(result.execution_result)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_missing_confirmation_callback_aborts(self):
        """If changes exist but confirm_callback is omitted, execution strictly aborts with 0 writes."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=None,
            dry_run=False,
        )

        self.assertEqual(result.status, ExecutionStatus.ABORTED)
        self.assertFalse(result.is_success)
        self.assertFalse(result.confirmed)
        self.assertIn("confirm_callback was not provided", result.error)
        self.assertIsNone(result.execution_result)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_rejected_confirmation_aborts(self):
        """If user callback returns False, execution strictly aborts with 0 writes."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        summary_inspected = None

        def callback(summary: PlanConfirmationSummary) -> bool:
            nonlocal summary_inspected
            summary_inspected = summary
            return False  # Reject

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=callback,
        )

        self.assertEqual(result.status, ExecutionStatus.ABORTED)
        self.assertFalse(result.is_success)
        self.assertFalse(result.confirmed)
        self.assertIn("rejected", result.error.lower())
        self.assertIsNotNone(summary_inspected)
        self.assertEqual(summary_inspected.total_packets, 10)
        self.assertIn("WARNING", summary_inspected.warning)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_confirmation_exception_aborts_safely(self):
        """If user callback raises an exception, execution aborts safely with 0 writes."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        def bad_callback(summary: PlanConfirmationSummary) -> bool:
            raise RuntimeError("User dialogue dismissed abruptly")

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=bad_callback,
        )

        self.assertEqual(result.status, ExecutionStatus.ABORTED)
        self.assertFalse(result.is_success)
        self.assertIn("Confirmation callback raised an exception", result.error)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_confirmed_modification_executes_and_verifies(self):
        """When confirmed, executor sends chunks, validates ACKs, and performs readback."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        new_rec = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        mod_profile.remap.set_slot(1, new_rec)

        applied_state = self.baseline.clone()
        applied_state.remap_l1.set_slot(1, new_rec)

        def mock_readback(tr, timeout):
            return applied_state

        summary_received: PlanConfirmationSummary | None = None

        def callback(summary: PlanConfirmationSummary) -> bool:
            nonlocal summary_received
            summary_received = summary
            # Verify fields inside callback
            self.assertEqual(summary.profile_id, 1)
            self.assertEqual(summary.changed_subsystems, ["remap_l1"])
            self.assertEqual(summary.total_packets, 10)
            self.assertEqual(len(summary.steps_summary), 1)
            self.assertEqual(summary.steps_summary[0]["opcode"], 0x22)
            return True

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=callback,
            readback_func=mock_readback,
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertTrue(result.confirmed)
        self.assertIsNotNone(summary_received)
        self.assertIsNotNone(result.execution_result)
        self.assertEqual(result.execution_result.packets_sent, 10)
        self.assertEqual(result.execution_result.acks_received, 10)
        self.assertTrue(result.execution_result.readback_verified)
        self.assertEqual(len(self.transport.recorded_reports), 10)

    def test_executor_failure_propagates_faithfully(self):
        """If executor encounters an ACK error, it fails immediately and reports the error."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        # Force wrong ACK opcode in transport
        self.transport.simulate_wrong_ack_opcode = 0xFE

        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=lambda s: True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.is_success)
        self.assertTrue(result.confirmed)
        self.assertIn("Invalid ACK opcode 0xFE", result.error)
        self.assertEqual(result.execution_result.packets_sent, 1)
        self.assertEqual(len(self.transport.recorded_reports), 1)

    def test_readback_mismatch_failure_propagates(self):
        """If readback shows unchanged state, result reports FAILED with diff."""
        mod_profile = self.manager.create_profile_from_state(
            self.baseline, profile_id=1, name="Modified"
        )
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        # Readback returns unchanged baseline
        result = apply_profile(
            transport=self.transport,
            profile=mod_profile,
            current_state=self.baseline,
            confirm_callback=lambda s: True,
            readback_func=lambda tr, to: self.baseline.clone(),
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.is_success)
        self.assertIn("Readback verification mismatch", result.error)
        self.assertFalse(result.execution_result.readback_verified)

    def test_state_collection_failure_handled(self):
        """If device state collection raises an error, result is FAILED with 0 writes."""
        # Device is closed in transport
        self.transport.close()

        profile = self.manager.create_profile_from_state(self.baseline, profile_id=1)

        result = apply_profile(
            transport=self.transport,
            profile=profile,
            current_state=None,  # triggers collect_device_state
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertFalse(result.is_success)
        self.assertIn("Failed to collect current device state", result.error)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_profile_applicator_class(self):
        """Test ProfileApplicator service methods apply() and preview()."""
        applicator = ProfileApplicator(self.transport, profile_manager=self.manager)

        # 1. Preview
        mod_profile = self.manager.create_profile_from_state(self.baseline, profile_id=2, name="P2")
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        preview_res = applicator.preview(mod_profile, current_state=self.baseline)
        self.assertEqual(preview_res.status, ExecutionStatus.SUCCESS)
        self.assertTrue(preview_res.dry_run)
        self.assertEqual(len(self.transport.recorded_reports), 0)

        # 2. Apply (with reject)
        apply_res = applicator.apply(
            mod_profile,
            current_state=self.baseline,
            confirm_callback=lambda s: False,
        )
        self.assertEqual(apply_res.status, ExecutionStatus.ABORTED)
        self.assertEqual(len(self.transport.recorded_reports), 0)

    def test_profile_manager_delegation(self):
        """Test manager.apply_profile() delegation."""
        mod_profile = self.manager.create_profile_from_state(self.baseline, profile_id=3, name="P3")
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        result = self.manager.apply_profile(
            self.transport,
            mod_profile,
            current_state=self.baseline,
            dry_run=True,
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertTrue(result.dry_run)

    def test_format_text_outputs(self):
        """Test format_text on PlanConfirmationSummary and ApplyProfileResult."""
        mod_profile = self.manager.create_profile_from_state(self.baseline, profile_id=1, name="Test")
        mod_profile.remap.set_slot(
            1, KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)
        )

        res = apply_profile(
            self.transport,
            mod_profile,
            current_state=self.baseline,
            dry_run=True,
        )
        text_summary = res.confirmation_summary.format_text()
        self.assertIn("PROFILE APPLICATION CONFIRMATION SUMMARY", text_summary)
        self.assertIn("WARNING", text_summary)

        text_result = res.format_text()
        self.assertIn("APPLY PROFILE RESULT: SUCCESS", text_result)
        self.assertIn("Dry Run Mode:  YES", text_result)


if __name__ == "__main__":
    unittest.main()
