"""
Physical Hardware Integration Test: Remap L1 A -> B -> A via ProfileWritePlan Executor.

SAFETY POLICY:
- Strictly guarded: SKIPPED by default during automated test execution.
- Only executes if KEYBOARD_PHYSICAL_TEST=1 is explicitly set in the environment.
- Tests closed-loop physical writes against physical IO Type 84 keyboard:
  1. Reads initial hardware state (Layer 1 keymap).
  2. Modifies slot 50 (Key A -> Key B) via ProfileWritePlan + ProfilePlanExecutor.
  3. Validates post-write readback confirms Key B.
  4. Restores original keymap (Key B -> Key A) via ProfileWritePlan + ProfilePlanExecutor.
  5. Validates final post-write readback confirms exact byte equality with initial state.
"""

from __future__ import annotations

import os
import sys
import unittest

from keyboard_re.models.state import DeviceState, Profile, collect_device_state
from keyboard_re.protocol.executor import ExecutionStatus, execute_profile_write_plan
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.transport.native_hid import NativeHidTransport


def requires_physical_keyboard_write(cls_or_func):
    """
    Decorator requiring explicit environment opt-in for physical hardware write tests.
    """
    is_enabled = os.environ.get("KEYBOARD_PHYSICAL_TEST") == "1"
    return unittest.skipUnless(
        is_enabled,
        "Physical hardware write test skipped. Explicitly enable with KEYBOARD_PHYSICAL_TEST=1",
    )(cls_or_func)


@requires_physical_keyboard_write
class TestProfilePhysicalRemapABA(unittest.TestCase):
    """Physical hardware closed-loop Remap L1 A -> B -> A test."""

    def setUp(self):
        self.transport = NativeHidTransport()
        try:
            self.transport.open()
        except Exception as e:
            self.skipTest(f"Failed to open physical HID device: {e}")

    def tearDown(self):
        if self.transport and self.transport.is_connected:
            self.transport.close()

    def test_physical_remap_l1_a_to_b_to_a(self):
        """Execute physical A -> B -> A remap test with closed-loop readback."""
        test_slot = 50  # Slot 50 is Key 'A' (Bank 3, Col 2) in standard matrix

        # Step 1: Read initial hardware state
        print("\n[STEP 1] Reading initial keyboard state...")
        initial_state = collect_device_state(self.transport, timeout=1.0)
        self.assertIsNotNone(initial_state.remap_l1, "Initial state missing remap_l1")

        orig_record = initial_state.remap_l1.slots[test_slot]
        orig_bytes = bytes(initial_state.remap_l1.raw_bytes)
        print(f"  Slot {test_slot} initial scancode: 0x{orig_record.scancode:02X} ({orig_record.hid_name})")

        # Step 2: Prepare Target Profile B (Slot 50 -> Key B, scancode 0x05)
        print("\n[STEP 2] Planning Phase 1: Remap A -> B (scancode 0x05)...")
        target_b = initial_state.create_profile(1, "Physical Test Phase B")
        rec_b = KeyRemapRecord(
            prefix=orig_record.prefix,
            scancode=0x05,  # Key 'B'
            special=0,
            function_type=0x02,
        )
        target_b.remap.set_slot(test_slot, rec_b)

        plan_ab = build_profile_write_plan(initial_state, target_b)
        self.assertEqual(len(plan_ab.steps), 1)
        self.assertEqual(plan_ab.steps[0].subsystem, "remap_l1")
        self.assertEqual(plan_ab.total_packets, 10)

        # Step 3: Execute Plan A -> B with closed-loop readback
        print("[STEP 3] Transmitting Plan A -> B (10 chunks) and validating readback...")
        result_ab = execute_profile_write_plan(
            transport=self.transport,
            plan=plan_ab,
            target_profile=target_b,
            timeout=1.0,
        )
        print(result_ab.format_text())
        self.assertEqual(result_ab.status, ExecutionStatus.SUCCESS, f"Phase A->B failed: {result_ab.error}")
        self.assertTrue(result_ab.readback_verified)
        self.assertEqual(result_ab.packets_sent, 10)
        self.assertEqual(result_ab.acks_received, 10)

        # Confirm independently via direct collect_device_state
        state_after_b = collect_device_state(self.transport, timeout=1.0)
        self.assertEqual(
            state_after_b.remap_l1.slots[test_slot].scancode,
            0x05,
            "Slot 50 did not persist Key B",
        )
        print("  Confirmed: Slot 50 is physically Key B on device.")

        # Step 4: Prepare Restore Profile A (Slot 50 -> original record)
        print("\n[STEP 4] Planning Phase 2: Restoring original binding B -> A...")
        target_a = state_after_b.create_profile(1, "Physical Restore A")
        target_a.remap.set_slot(test_slot, orig_record)

        plan_ba = build_profile_write_plan(state_after_b, target_a)
        self.assertEqual(len(plan_ba.steps), 1)
        self.assertEqual(plan_ba.steps[0].subsystem, "remap_l1")

        # Step 5: Execute Plan B -> A with closed-loop readback
        print("[STEP 5] Transmitting Plan B -> A (10 chunks) and restoring original state...")
        result_ba = execute_profile_write_plan(
            transport=self.transport,
            plan=plan_ba,
            target_profile=target_a,
            timeout=1.0,
        )
        print(result_ba.format_text())
        self.assertEqual(result_ba.status, ExecutionStatus.SUCCESS, f"Phase B->A restore failed: {result_ba.error}")
        self.assertTrue(result_ba.readback_verified)

        # Step 6: Final Verification - exact byte-level match with initial state
        print("\n[STEP 6] Final readback verification...")
        final_state = collect_device_state(self.transport, timeout=1.0)
        self.assertEqual(
            final_state.remap_l1.slots[test_slot].scancode,
            orig_record.scancode,
            "Slot 50 did not restore to original scancode",
        )
        self.assertEqual(
            final_state.remap_l1.raw_bytes,
            orig_bytes,
            "Final Remap L1 buffer does not match initial state 100% byte-exact",
        )
        print("  SUCCESS: Keyboard Remap L1 fully restored to original byte-exact state!")


def run_manual():
    """Manual CLI entry point for running the physical Remap test."""
    print("=" * 70)
    print("PHYSICAL HARDWARE REMAP L1 A -> B -> A TEST")
    print("=" * 70)
    print("WARNING: This test will write to the physical keyboard flash memory.")
    print("It will temporarily remap Key A (slot 50) to Key B, verify, then restore Key A.")
    print("=" * 70)

    val = input("Type 'CONFIRM' to execute physical test: ").strip()
    if val != "CONFIRM":
        print("Aborted by user.")
        return

    os.environ["KEYBOARD_PHYSICAL_TEST"] = "1"
    suite = unittest.TestSuite()
    suite.addTest(TestProfilePhysicalRemapABA("test_physical_remap_l1_a_to_b_to_a"))
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--manual":
        run_manual()
    else:
        unittest.main()
