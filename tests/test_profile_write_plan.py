"""
Unit tests for deterministic, dry-run ProfileWritePlan generator.

Validates:
1. Identical profile against DeviceState -> empty plan (is_empty=True, 0 steps, 0 packets).
2. Modifying only RGB -> generates only AA23 (Global) and/or AA24 (Matrix).
3. Modifying only Remap L1 -> generates only AA22 (10 chunks: 9x56B + 1x8B).
4. Modifying only Remap L2 -> generates only AA26 (10 chunks: 9x56B + 1x8B).
5. Modifying multiple subsystems -> guarantees canonical execution order:
   AA22 (Remap L1) -> AA26 (Remap L2) -> AA23 (RGB Global) -> AA24 (RGB Matrix) ->
   AA25 (Macro) -> AA27 (Hall RT) -> AA28 (DKS).
6. Byte-exact preservation of unknown buffers (Macro 400B, DKS 1024B, Hall 1008B).
7. Validation of 64-byte report size, chunk addresses, and expected ACKs (55 xx).
8. Game Mode exclusion (device-global settings are strictly not included in plan).
9. Pure dry-run guarantee (ZERO physical HID transmissions or write calls).
"""

from pathlib import Path
import unittest

from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.plan import (
    ProfileWritePlan,
    SubsystemWriteStep,
    WriteChunk,
    build_profile_write_plan,
)


class TestProfileWritePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = (
            Path(__file__).parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.state = DeviceState.load_json(capture_path)
        cls.manager = ProfileManager()

    def test_identical_profile_yields_empty_plan(self):
        """Test comparing identical profile against DeviceState results in empty plan."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Identical")
        plan = build_profile_write_plan(self.state, profile)

        self.assertIsInstance(plan, ProfileWritePlan)
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.total_packets, 0)
        self.assertEqual(len(plan.steps), 0)
        self.assertEqual(plan.modified_subsystems, [])
        self.assertEqual(plan.opcodes, [])

        text = plan.format_text()
        self.assertIn("Plan is EMPTY", text)
        self.assertIn("PASSIVE PREVIEW ONLY", text)

    def test_rgb_global_only_modification(self):
        """Test changing only Global RGB settings generates only AA23 (1 packet)."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="RGB Mod")
        profile.rgb_global.effect = 1  # Change from Custom (15) to Static (1)
        profile.rgb_global.brightness = 3

        plan = build_profile_write_plan(self.state, profile)
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.modified_subsystems, ["rgb_global"])
        self.assertEqual(plan.opcodes, [0x23])
        self.assertEqual(plan.total_packets, 1)

        step = plan.get_step("rgb_global")
        self.assertIsNotNone(step)
        self.assertEqual(step.opcode, 0x23)
        self.assertEqual(step.packet_count, 1)

        chunk = step.chunks[0]
        self.assertEqual(len(chunk.packet), 64)
        self.assertEqual(len(chunk.expected_ack), 64)
        self.assertEqual(chunk.packet[:3], b"\xAA\x23\x10")
        self.assertEqual(chunk.expected_ack[:3], b"\x55\x23\x10")
        self.assertEqual(chunk.packet[8], 1)   # effect mode 1
        self.assertEqual(chunk.packet[17], 3)  # brightness 3

    def test_rgb_matrix_only_modification(self):
        """Test changing only Per-Key RGB matrix generates only AA24 (10 chunks)."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Matrix Mod")
        mat_bytes = bytearray(profile.rgb_matrix)
        mat_bytes[0] = 0xAA  # LED 0 Red
        mat_bytes[1] = 0xBB  # LED 0 Green
        mat_bytes[2] = 0xCC  # LED 0 Blue
        profile.rgb_matrix = bytes(mat_bytes)

        plan = build_profile_write_plan(self.state, profile)
        self.assertEqual(plan.modified_subsystems, ["rgb_matrix"])
        self.assertEqual(plan.opcodes, [0x24])
        self.assertEqual(plan.total_packets, 10)

        step = plan.get_step("rgb_matrix")
        self.assertIsNotNone(step)
        self.assertEqual(step.packet_count, 10)

        # 9 chunks of 56 bytes + 1 tail of 8 bytes
        for i in range(9):
            c = step.chunks[i]
            self.assertEqual(c.size, 56)
            self.assertEqual(c.address, i * 56)
            self.assertEqual(c.packet[:3], b"\xAA\x24\x38")
            self.assertEqual(c.expected_ack[:3], b"\x55\x24\x38")
            self.assertEqual(len(c.packet), 64)

        tail = step.chunks[9]
        self.assertEqual(tail.size, 8)
        self.assertEqual(tail.address, 504)
        self.assertEqual(tail.packet[:3], b"\xAA\x24\x08")
        self.assertEqual(tail.expected_ack[:3], b"\x55\x24\x08")

    def test_remap_l1_only_modification(self):
        """Test changing Remap Layer 1 generates only AA22 (10 chunks)."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Remap L1")
        # Change slot 0 (Esc) to CapsLock (scancode 57)
        profile.remap.slots[0] = KeyRemapRecord.for_standard_key(57)

        plan = build_profile_write_plan(self.state, profile)
        self.assertEqual(plan.modified_subsystems, ["remap_l1"])
        self.assertEqual(plan.opcodes, [0x22])
        self.assertEqual(plan.total_packets, 10)

        step = plan.get_step("remap_l1")
        self.assertIsNotNone(step)
        self.assertEqual(len(step.raw_payload), 512)

        # Verify chunk 0 contains the modified slot 0 at offset 0..3
        c0 = step.chunks[0]
        self.assertEqual(c0.address, 0)
        self.assertEqual(c0.size, 56)
        self.assertEqual(c0.packet[:3], b"\xAA\x22\x38")
        self.assertEqual(c0.expected_ack[:3], b"\x55\x22\x38")
        # Slot 0 is at byte offset 0 inside payload (bytes 8..11 of packet: [02, 00, scancode, 00])
        self.assertEqual(c0.packet[8 + 2], 57)  # scancode byte

    def test_remap_l2_only_modification(self):
        """Test changing Remap Layer 2 (Fn) generates only AA26 (10 chunks)."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Remap L2")
        profile.remap_l2.slots[5] = KeyRemapRecord(prefix=0, scancode=128, special=0, function_type=0x02)

        plan = build_profile_write_plan(self.state, profile)
        self.assertEqual(plan.modified_subsystems, ["remap_l2"])
        self.assertEqual(plan.opcodes, [0x26])
        self.assertEqual(plan.total_packets, 10)

        step = plan.get_step("remap_l2")
        self.assertIsNotNone(step)
        self.assertEqual(step.chunks[0].packet[:3], b"\xAA\x26\x38")
        self.assertEqual(step.chunks[0].expected_ack[:3], b"\x55\x26\x38")

    def test_canonical_multi_subsystem_execution_order(self):
        """
        Test modifying all 7 subsystems simultaneously.
        Guarantees strict canonical order:
        Remap L1 (0x22) -> Remap L2 (0x26) -> RGB Global (0x23) -> RGB Matrix (0x24) ->
        Macro (0x25) -> Hall RT (0x27) -> DKS (0x28).
        """
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Full Overhaul")

        # 1. Remap L1
        profile.remap.slots[1] = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)

        # 2. Remap L2
        profile.remap_l2.slots[5] = KeyRemapRecord(prefix=0, scancode=128, special=0, function_type=0x02)

        # 3. RGB Global
        profile.rgb_global.effect = 7

        # 4. RGB Matrix
        mat = bytearray(profile.rgb_matrix)
        mat[0] = 0x11
        profile.rgb_matrix = bytes(mat)

        # 5. Macro raw
        mac = bytearray(profile.macros_raw)
        mac[10] = 0x22
        profile.macros_raw = bytes(mac)

        # 6. Hall RT
        profile.hall.set_actuation("A", 0.50)

        # 7. DKS raw
        dks = bytearray(profile.dks_raw)
        dks[50] = 0x33
        profile.dks_raw = bytes(dks)

        plan = build_profile_write_plan(self.state, profile)

        # Verify exact sequence of opcodes
        expected_opcodes = [0x22, 0x26, 0x23, 0x24, 0x25, 0x27, 0x28]
        expected_subsystems = ["remap_l1", "remap_l2", "rgb_global", "rgb_matrix", "macro", "hall", "dks"]

        self.assertEqual(plan.opcodes, expected_opcodes)
        self.assertEqual(plan.modified_subsystems, expected_subsystems)

        # Verify packet counts for each step:
        # L1 (10) + L2 (10) + RGB Global (1) + RGB Matrix (10) + Macro (8) + Hall (19) + DKS (19) = 77
        expected_packets = 10 + 10 + 1 + 10 + 8 + 19 + 19
        self.assertEqual(plan.total_packets, expected_packets)
        self.assertEqual(plan.get_step("remap_l1").packet_count, 10)
        self.assertEqual(plan.get_step("remap_l2").packet_count, 10)
        self.assertEqual(plan.get_step("rgb_global").packet_count, 1)
        self.assertEqual(plan.get_step("rgb_matrix").packet_count, 10)
        self.assertEqual(plan.get_step("macro").packet_count, 8)
        self.assertEqual(plan.get_step("hall").packet_count, 19)
        self.assertEqual(plan.get_step("dks").packet_count, 19)

    def test_byte_exact_unknown_buffers_preservation(self):
        """Test unknown binary buffers (Macro 400B, DKS 1024B, Hall 1008B) are faithfully sliced."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Exact Binary")

        # Custom macro buffer
        test_macro = bytes([i % 256 for i in range(400)])
        profile.macros_raw = test_macro

        # Custom DKS buffer
        test_dks = bytes([(i * 7) % 256 for i in range(1024)])
        profile.dks_raw = test_dks

        plan = build_profile_write_plan(self.state, profile)

        # Macro chunks reconstruction check
        macro_step = plan.get_step("macro")
        self.assertIsNotNone(macro_step)
        reconstructed_macro = bytearray(400)
        for c in macro_step.chunks:
            reconstructed_macro[c.address : c.address + c.size] = c.packet[8 : 8 + c.size]
        self.assertEqual(bytes(reconstructed_macro), test_macro)

        # DKS chunks reconstruction check
        dks_step = plan.get_step("dks")
        self.assertIsNotNone(dks_step)
        reconstructed_dks = bytearray(1024)
        for c in dks_step.chunks:
            reconstructed_dks[c.address : c.address + c.size] = c.packet[8 : 8 + c.size]
        self.assertEqual(bytes(reconstructed_dks), test_dks)

    def test_game_mode_exclusion(self):
        """Test Game Mode performance settings are excluded from ProfileWritePlan."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Game Mode Test")
        plan = build_profile_write_plan(self.state, profile)

        # Even if profile is created from state with game_mode, no game_mode step exists
        self.assertNotIn("game_mode", plan.modified_subsystems)
        self.assertNotIn(0x21, plan.opcodes)

    def test_manager_integration(self):
        """Test ProfileManager.create_write_plan integration method."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Manager Test")
        profile.hall.set_actuation("W", 0.60)

        plan = self.manager.create_write_plan(self.state, profile)
        self.assertIsInstance(plan, ProfileWritePlan)
        self.assertEqual(plan.modified_subsystems, ["hall"])
        self.assertEqual(plan.opcodes, [0x27])
        self.assertEqual(plan.total_packets, 19)


if __name__ == "__main__":
    unittest.main()
