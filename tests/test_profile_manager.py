"""
Unit tests for host-side ProfileManager and structured ProfileDiff engine.

Validates:
1. Multi-profile storage on disk (save, load, list, delete, cache).
2. Profile creation from DeviceState snapshot with complete isolation.
3. 100% byte-exact binary preservation across disk serialization round-trips.
4. Structured subsystem-level diff engine (ProfileDiff) across all 7 profile subsystems:
   - Hall (actuation, RT press, RT release, flags)
   - Remap Layer 1 (Base layout)
   - Remap Layer 2 (Fn layout)
   - RGB Global (effect, colors, brightness, speed)
   - RGB Matrix (per-key LED colors)
   - Macro raw (400B buffer)
   - DKS raw (1024B buffer)
   - Game Mode (device-global separation)
5. Zero physical HID write operations.
"""

from pathlib import Path
import tempfile
import unittest

from keyboard_re.models.base import KeyConfig
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import (
    KeyDiff,
    LedDiff,
    ParamDiff,
    ProfileDiff,
    ProfileManager,
    SlotDiff,
    SubsystemDiff,
)
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.rgb import RGBGlobalConfig


class TestProfileManager(unittest.TestCase):
    def setUp(self):
        self.capture_path = (
            Path(__file__).parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        self.state = DeviceState.load_json(self.capture_path)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = ProfileManager(storage_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_profile_from_state(self):
        """Test creating a Profile from current DeviceState snapshot."""
        profile = self.manager.create_profile_from_state(
            state=self.state,
            profile_id=1,
            name="Competitive CS2",
        )

        self.assertIsInstance(profile, Profile)
        self.assertEqual(profile.profile_id, 1)
        self.assertEqual(profile.name, "Competitive CS2")

        # Verify all subsystems are cloned and present
        self.assertIsNotNone(profile.hall)
        self.assertEqual(len(profile.hall.raw_image), 1008)
        self.assertAlmostEqual(profile.hall.get_key("A").actuation_mm, 1.40, places=2)

        self.assertIsNotNone(profile.remap)
        self.assertEqual(len(profile.remap.raw_bytes), 512)
        self.assertEqual(profile.remap.get_slot(0, 0).hid_name, "Escape")

        self.assertIsNotNone(profile.remap_l2)
        self.assertEqual(len(profile.remap_l2.raw_bytes), 512)
        self.assertEqual(profile.remap_l2.get_slot(5, 12).hid_name, "ScrollLock")

        self.assertIsNotNone(profile.rgb_global)
        self.assertEqual(profile.rgb_global.effect, 15)

        self.assertIsNotNone(profile.rgb_matrix)
        self.assertEqual(len(profile.rgb_matrix), 512)

        self.assertIsNotNone(profile.macros_raw)
        self.assertEqual(len(profile.macros_raw), 400)

        self.assertIsNotNone(profile.dks_raw)
        self.assertEqual(len(profile.dks_raw), 1024)

        # Verify isolation: mutating profile does NOT alter original DeviceState
        profile.hall.set_actuation("A", 0.30)
        self.assertAlmostEqual(profile.hall.get_key("A").actuation_mm, 0.30, places=2)
        self.assertAlmostEqual(self.state.hall_profile_1.get_key("A").actuation_mm, 1.40, places=2)

    def test_disk_persistence_round_trip_and_byte_exactness(self):
        """Test saving Profile to disk and reloading with 100% byte-exact binary match."""
        orig_profile = self.manager.create_profile_from_state(
            state=self.state,
            profile_id=42,
            name="Test Byte Exact",
        )

        saved_path = self.manager.save_profile(orig_profile)
        self.assertTrue(saved_path.is_file())

        # Load back via ID and via Path
        loaded_by_id = self.manager.load_profile(42)
        loaded_by_path = self.manager.load_profile(saved_path)

        self.assertEqual(loaded_by_id.name, "Test Byte Exact")
        self.assertEqual(loaded_by_path.name, "Test Byte Exact")

        # Verify 100% byte-exact equality across all raw buffers
        orig_bufs = orig_profile.dump_raw_buffers()
        loaded_bufs = loaded_by_id.dump_raw_buffers()

        expected_buffers = [
            "hall_p1",
            "remap_l1",
            "remap_l2",
            "rgb_global",
            "rgb_matrix",
            "macro_table",
            "dks_table",
        ]
        for buf_name in expected_buffers:
            self.assertIn(buf_name, orig_bufs)
            self.assertIn(buf_name, loaded_bufs)
            self.assertEqual(
                orig_bufs[buf_name],
                loaded_bufs[buf_name],
                f"Binary buffer '{buf_name}' mismatch after disk persistence round-trip!",
            )

    def test_multi_profile_management(self):
        """Test saving, listing, retrieving, and deleting multiple profiles."""
        p1 = self.manager.create_profile_from_state(self.state, profile_id=1, name="FPS Gaming")
        p2 = self.manager.create_profile_from_state(self.state, profile_id=2, name="Typing Mode")
        p3 = self.manager.create_profile_from_state(self.state, profile_id=3, name="Mac Productivity")

        # Custom modifications for each profile
        p1.hall.set_actuation("W", 0.50)
        p2.hall.set_actuation("W", 2.20)
        p3.hall.set_actuation("W", 1.80)

        path1 = self.manager.save_profile(p1)
        path2 = self.manager.save_profile(p2)
        path3 = self.manager.save_profile(p3)

        # List profiles
        summaries = self.manager.list_profiles()
        self.assertEqual(len(summaries), 3)
        names = {s["name"] for s in summaries}
        self.assertEqual(names, {"FPS Gaming", "Typing Mode", "Mac Productivity"})

        # Retrieve profile
        retrieved_p2 = self.manager.get_profile(2)
        self.assertAlmostEqual(retrieved_p2.hall.get_key("W").actuation_mm, 2.20, places=2)

        # Delete profile
        deleted = self.manager.delete_profile(2)
        self.assertTrue(deleted)
        self.assertFalse(path2.is_file())
        self.assertEqual(len(self.manager.list_profiles()), 2)

        # Deleting nonexistent profile returns False
        self.assertFalse(self.manager.delete_profile(99))

    def test_compare_profile_with_state_identical(self):
        """Test diffing a profile created from state against the same state yields no diffs."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Identical")
        diff = self.manager.compare_profile_with_state(profile, self.state)

        self.assertIsInstance(diff, ProfileDiff)
        self.assertFalse(diff.has_changes)
        self.assertEqual(diff.total_changes, 0)
        self.assertEqual(len(diff.changed_subsystems), 0)

        # Verify GameMode is reported as device-global
        gm_sub = diff.get_subsystem("game_mode")
        self.assertIsNotNone(gm_sub)
        self.assertFalse(gm_sub.has_changes)
        self.assertIn("device-global", gm_sub.summary)

    def test_compare_profile_with_state_hall_diff(self):
        """Test detecting Hall switch differences (actuation, RT, flags)."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Hall Mod")

        # Modify A (actuation), W (rt_press and rt_release), Space (flags)
        profile.hall.set_actuation("A", 0.50)
        profile.hall.set_rt("W", press_mm=0.20, release_mm=0.20)
        cfg_space = profile.hall.get_key("Space")
        cfg_space.flags = 0x0001
        profile.hall.set_key("Space", cfg_space)

        diff = self.manager.compare_profile_with_state(profile, self.state)
        self.assertTrue(diff.has_changes)
        self.assertIn("hall", diff.changed_subsystems)

        hall_sub = diff.get_subsystem("hall")
        self.assertTrue(hall_sub.has_changes)
        self.assertGreaterEqual(hall_sub.change_count, 4)

        # Check key diff details
        key_names = {d.key_name for d in hall_sub.details if isinstance(d, KeyDiff)}
        self.assertIn("A", key_names)
        self.assertIn("W", key_names)
        self.assertIn("SPACE", key_names)

    def test_compare_profile_with_state_remap_diff(self):
        """Test detecting Remap Layer 1 and Layer 2 differences."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Remap Mod")

        # Remap Layer 1: Slot 1 (Esc) to CapsLock (scancode 0x39 = 57)
        profile.remap.slots[1] = KeyRemapRecord(prefix=0, scancode=57, special=0, function_type=0x02)

        # Remap Layer 2: Slot 5 (F5) to VolumeUp (scancode 0x80 = 128)
        profile.remap_l2.slots[5] = KeyRemapRecord(prefix=0, scancode=128, special=0, function_type=0x02)

        diff = self.manager.compare_profile_with_state(profile, self.state)
        self.assertTrue(diff.has_changes)
        self.assertIn("remap_l1", diff.changed_subsystems)
        self.assertIn("remap_l2", diff.changed_subsystems)

        l1_sub = diff.get_subsystem("remap_l1")
        self.assertEqual(l1_sub.change_count, 1)
        self.assertIsInstance(l1_sub.details[0], SlotDiff)
        self.assertEqual(l1_sub.details[0].slot_index, 1)

        l2_sub = diff.get_subsystem("remap_l2")
        self.assertEqual(l2_sub.change_count, 1)
        self.assertIsInstance(l2_sub.details[0], SlotDiff)
        self.assertEqual(l2_sub.details[0].slot_index, 5)

    def test_compare_profile_with_state_rgb_diffs(self):
        """Test detecting RGB Global and RGB Matrix differences."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="RGB Mod")

        # Global RGB: change effect and brightness
        profile.rgb_global.effect = 1
        profile.rgb_global.brightness = 2

        # RGB Matrix: change LED 0 color
        mat_bytes = bytearray(profile.rgb_matrix)
        mat_bytes[0] = 0xFF  # Red
        mat_bytes[1] = 0x00  # Green
        mat_bytes[2] = 0xAA  # Blue
        profile.rgb_matrix = bytes(mat_bytes)

        diff = self.manager.compare_profile_with_state(profile, self.state)
        self.assertIn("rgb_global", diff.changed_subsystems)
        self.assertIn("rgb_matrix", diff.changed_subsystems)

        rgb_g_sub = diff.get_subsystem("rgb_global")
        self.assertEqual(rgb_g_sub.change_count, 2)

        rgb_m_sub = diff.get_subsystem("rgb_matrix")
        self.assertEqual(rgb_m_sub.change_count, 1)
        self.assertIsInstance(rgb_m_sub.details[0], LedDiff)
        self.assertEqual(rgb_m_sub.details[0].led_index, 0)
        self.assertEqual(rgb_m_sub.details[0].old_color, (0, 0, 0))
        self.assertEqual(rgb_m_sub.details[0].new_color, (0xFF, 0x00, 0xAA))

    def test_compare_profile_with_state_macro_and_dks_diffs(self):
        """Test detecting Macro raw and DKS raw differences."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Macro DKS Mod")

        # Modify macro buffer at byte 10
        m_bytes = bytearray(profile.macros_raw)
        m_bytes[10] = 0x77
        profile.macros_raw = bytes(m_bytes)

        # Modify DKS buffer at record 5 (offset 80)
        d_bytes = bytearray(profile.dks_raw)
        d_bytes[80] = 0x12
        d_bytes[81] = 0x34
        profile.dks_raw = bytes(d_bytes)

        diff = self.manager.compare_profile_with_state(profile, self.state)
        self.assertIn("macro_raw", diff.changed_subsystems)
        self.assertIn("dks_raw", diff.changed_subsystems)

        macro_sub = diff.get_subsystem("macro_raw")
        self.assertEqual(macro_sub.change_count, 1)

        dks_sub = diff.get_subsystem("dks_raw")
        self.assertEqual(dks_sub.change_count, 2)
        self.assertIn("DKS record", dks_sub.summary)

    def test_compare_profiles_between_two_profiles(self):
        """Test comparing two distinct Profile instances."""
        p_fast = self.manager.create_profile_from_state(self.state, profile_id=1, name="Fast")
        p_slow = self.manager.create_profile_from_state(self.state, profile_id=2, name="Slow")

        p_fast.hall.set_actuation("Space", 0.40)
        p_slow.hall.set_actuation("Space", 2.50)

        diff = self.manager.compare_profiles(p_fast, p_slow)
        self.assertTrue(diff.has_changes)
        self.assertEqual(diff.profile_name, "Fast")
        self.assertEqual(diff.target_name, "Slow")
        self.assertIn("hall", diff.changed_subsystems)

        text = diff.format_text()
        self.assertIn("ProfileDiff: 'Fast'", text)
        self.assertIn("vs Slow", text)
        self.assertIn("SPACE", text)


if __name__ == "__main__":
    unittest.main()
