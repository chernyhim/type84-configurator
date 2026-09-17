"""
Physical Hardware Integration Test: RGB Global Static Mode A -> B -> A via ProfilePlanExecutor.

SAFETY POLICY:
- Strictly guarded: SKIPPED by default during automated test execution.
- Only executes if KEYBOARD_PHYSICAL_TEST=1 is explicitly set in the environment.
- Tests closed-loop physical writes against physical IO Type 84 keyboard:
  1. Reads initial hardware state (RGB Global + RGB Matrix).
  2. Saves byte-exact raw baseline buffers & SHA-256 hashes.
  3. Modifies only RGB Global: switches to EFFECT_STATIC (0x01) Pure Red (255, 0, 0), Single Color (color_mode=0).
  4. Transmits exactly 1 AA 23 10 report via ProfileWritePlan + ProfilePlanExecutor.
  5. Validates 55 23 10 ACK from device.
  6. Performs closed-loop readback verifying effect=0x01, primary=(255,0,0), color_mode=0.
  7. Restores exact original baseline RGB Global configuration.
  8. Transmits exactly 1 AA 23 10 restore report and validates ACK.
  9. Validates final post-restore readback confirms 100% byte-exact SHA-256 match with initial state.
"""

from __future__ import annotations

import copy
import hashlib
import os
import sys
import unittest

from keyboard_re.models.state import DeviceState, Profile, collect_device_state
from keyboard_re.protocol.executor import ExecutionStatus, execute_profile_write_plan
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.rgb import EFFECT_STATIC, RGBGlobalConfig
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
class TestProfilePhysicalRGBABA(unittest.TestCase):
    """Physical hardware closed-loop RGB Global A -> B -> A test."""

    def setUp(self):
        self.transport = NativeHidTransport()
        try:
            self.transport.open()
        except Exception as e:
            self.skipTest(f"Failed to open physical HID device: {e}")

    def tearDown(self):
        if self.transport and self.transport.is_connected:
            self.transport.close()

    def test_physical_rgb_static_a_to_b_to_a(self):
        """Execute physical A -> B -> A RGB test with closed-loop readback."""
        # Step 1: Read initial hardware state
        print("\n[STEP 1] Reading initial keyboard state and capturing baseline...")
        initial_state = collect_device_state(self.transport, timeout=1.0)
        self.assertIsNotNone(initial_state.rgb_global, "Initial state missing rgb_global")
        self.assertIsNotNone(initial_state.rgb_matrix_raw, "Initial state missing rgb_matrix_raw")

        orig_global = copy.deepcopy(initial_state.rgb_global)
        baseline_rgb_bytes = initial_state.dump_raw_buffers().get("rgb_global", b"")
        baseline_sha = hashlib.sha256(baseline_rgb_bytes).hexdigest()
        baseline_matrix_sha = hashlib.sha256(bytes(initial_state.rgb_matrix_raw)).hexdigest()

        print(f"  Baseline Effect Mode: 0x{orig_global.effect:02X} ({orig_global.effect})")
        print(f"  Baseline Primary RGB: {orig_global.primary}")
        print(f"  Baseline Secondary:   {orig_global.secondary}")
        print(f"  Baseline Color Mode:  {orig_global.color_mode} ({'Single Color' if orig_global.color_mode == 0 else 'Rainbow RGB'})")
        print(f"  Baseline Brightness:  {orig_global.brightness}")
        print(f"  Baseline Speed:       {orig_global.speed}")
        print(f"  Baseline RGB SHA-256: {baseline_sha}")
        print(f"  Baseline Matrix SHA:  {baseline_matrix_sha}")

        # Step 2: Prepare Target Profile B (EFFECT_STATIC 0x01, Pure Red, Single Color)
        print("\n[STEP 2] Planning Phase 1: RGB Baseline -> Static Pure Red (0x01)...")
        target_b = initial_state.create_profile(1, "Physical RGB Static Red")
        target_b.rgb_global = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),       # Pure Red
            secondary=(0, 0, 0),
            brightness=orig_global.brightness,
            speed=3,                   # Default speed
            color_mode=0,              # Single Color
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )

        plan_ab = build_profile_write_plan(initial_state, target_b)
        self.assertEqual(len(plan_ab.steps), 1, "Plan must contain exactly 1 step (rgb_global)")
        self.assertEqual(plan_ab.steps[0].subsystem, "rgb_global")
        self.assertEqual(plan_ab.total_packets, 1, "Plan must contain exactly 1 packet (AA 23 10)")

        # Step 3: Execute Plan A -> B with closed-loop readback
        print("[STEP 3] Transmitting Plan A -> B (1 report) and validating ACK...")
        result_ab = execute_profile_write_plan(
            transport=self.transport,
            plan=plan_ab,
            target_profile=target_b,
            timeout=1.0,
        )
        print(result_ab.format_text())
        self.assertEqual(result_ab.status, ExecutionStatus.SUCCESS, f"Phase A->B failed: {result_ab.error}")
        self.assertTrue(result_ab.readback_verified, "Post-write readback verification failed for Target B")
        self.assertEqual(result_ab.packets_sent, 1)
        self.assertEqual(result_ab.acks_received, 1)

        # Step 4: Confirm independently via direct collect_device_state
        print("\n[STEP 4] Direct readback check after Target B...")
        state_after_b = collect_device_state(self.transport, timeout=1.0)
        self.assertEqual(state_after_b.rgb_global.effect, EFFECT_STATIC, "Device effect is not 0x01 (Static)")
        self.assertEqual(state_after_b.rgb_global.primary, (255, 0, 0), "Device primary color is not Pure Red")
        self.assertEqual(state_after_b.rgb_global.color_mode, 0, "Device color_mode is not Single Color (0)")
        print("  Confirmed: Keyboard is physically in Static Pure Red mode!")
        print("  [VISUAL CHECK] Keyboard LEDs should now be solid RED.")

        # Step 5: Prepare Restore Profile A (exact original baseline)
        print("\n[STEP 5] Planning Phase 2: Restoring exact original RGB baseline...")
        target_a = state_after_b.create_profile(1, "Physical Restore RGB Baseline")
        target_a.rgb_global = orig_global

        plan_ba = build_profile_write_plan(state_after_b, target_a)
        self.assertEqual(len(plan_ba.steps), 1)
        self.assertEqual(plan_ba.steps[0].subsystem, "rgb_global")
        self.assertEqual(plan_ba.total_packets, 1)

        # Step 6: Execute Plan B -> A with closed-loop readback
        print("[STEP 6] Transmitting Plan B -> A (1 report) and restoring original state...")
        result_ba = execute_profile_write_plan(
            transport=self.transport,
            plan=plan_ba,
            target_profile=target_a,
            timeout=1.0,
        )
        print(result_ba.format_text())
        self.assertEqual(result_ba.status, ExecutionStatus.SUCCESS, f"Phase B->A restore failed: {result_ba.error}")
        self.assertTrue(result_ba.readback_verified, "Post-restore readback verification failed")

        # Step 7: Final Verification - exact byte-level match with initial state
        print("\n[STEP 7] Final readback verification and SHA-256 baseline comparison...")
        final_state = collect_device_state(self.transport, timeout=1.0)
        final_rgb_bytes = final_state.dump_raw_buffers().get("rgb_global", b"")
        final_sha = hashlib.sha256(final_rgb_bytes).hexdigest()
        final_matrix_sha = hashlib.sha256(bytes(final_state.rgb_matrix_raw)).hexdigest()

        self.assertEqual(final_state.rgb_global.effect, orig_global.effect, "Effect mode did not restore")
        self.assertEqual(final_state.rgb_global.primary, orig_global.primary, "Primary color did not restore")
        self.assertEqual(final_state.rgb_global.color_mode, orig_global.color_mode, "Color mode did not restore")
        self.assertEqual(final_matrix_sha, baseline_matrix_sha, "RGB Matrix buffer unexpectedly altered")
        self.assertEqual(
            final_sha,
            baseline_sha,
            f"Final RGB Global SHA-256 mismatch!\nExpected: {baseline_sha}\nGot:      {final_sha}",
        )
        print("  SUCCESS: Keyboard RGB Global configuration 100% byte-exact restored!")
        print(f"  Baseline SHA-256: {baseline_sha}")
        print(f"  Final SHA-256:    {final_sha} (MATCH)")


def run_manual():
    """Manual CLI entry point for running the physical RGB test."""
    print("=" * 70)
    print("PHYSICAL HARDWARE RGB GLOBAL A -> B -> A TEST")
    print("=" * 70)
    print("WARNING: This test will write to the physical keyboard RGB configuration.")
    print("It will temporarily switch RGB to Static Pure Red (0x01), verify, then restore original.")
    print("=" * 70)

    val = input("Type 'CONFIRM' to execute physical test: ").strip()
    if val != "CONFIRM":
        print("Aborted by user.")
        return

    os.environ["KEYBOARD_PHYSICAL_TEST"] = "1"
    suite = unittest.TestSuite()
    suite.addTest(TestProfilePhysicalRGBABA("test_physical_rgb_static_a_to_b_to_a"))
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--manual":
        run_manual()
    else:
        unittest.main()
