"""
Unit tests for Custom Per-Key RGB (Effect 0x80) readback equivalence and normalization.

Tests the semantic equivalence rules:
- Target EFFECT_CUSTOM (0x80) vs firmware readback 0x14 / 0x15 is equivalent.
- Runtime status fields (primary #FFFFFF, color_mode 1) in Custom mode do not trigger mismatches.
- Strict validation is preserved for 512-byte RGB Matrix, LED_ID, brightness, and speed.
- Normal effects (e.g. 0x0F) vs 0x14 strictly fail.
- 0x14 is not added to RGB_EFFECT_CATALOG as a user effect.
"""

from __future__ import annotations

import copy
from pathlib import Path
import unittest

from keyboard_re.models.rgb_matrix import RGBMatrix
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.read import parse_rgb_global_read_response
from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    EFFECT_CUSTOM_READBACK_ALIASES,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    EFFECTS_WITH_FIXED_RAINBOW_READBACK,
    RGB_EFFECT_CATALOG,
    RGBGlobalConfig,
    is_custom_effect_readback_equivalent,
    is_effect_fixed_rainbow_readback,
)


class TestRGBReadbackEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = (
            Path(__file__).resolve().parents[2]
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.base_state = DeviceState.load_json(capture_path)

    def setUp(self):
        self.pm = ProfileManager()

        # Construct target custom profile (Effect 0x80, red W key in matrix)
        self.target_custom_profile = self.base_state.create_profile(1, name="Target Custom Profile")
        self.target_custom_profile.rgb_global = RGBGlobalConfig(
            effect=EFFECT_CUSTOM,       # 0x80
            primary=(255, 0, 0),         # #FF0000
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=0,                # Single Color
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )

        matrix = RGBMatrix()
        matrix.set_led(35, (255, 0, 0)) # Key W = slot 35
        self.target_matrix_bytes = matrix.to_raw()
        self.target_custom_profile.rgb_matrix = self.target_matrix_bytes

    def test_custom_target_0x80_readback_0x14_passes_if_matrix_exact(self):
        """Custom target 0x80 + readback 0x14 => RGB Global verification passes, matrix exact."""
        state = self.base_state.clone()
        # Firmware readback: effect=0x14, primary=#FFFFFF, color_mode=1
        state.rgb_global = RGBGlobalConfig(
            effect=0x14,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )
        state.rgb_matrix_raw = bytearray(self.target_matrix_bytes)

        diff = self.pm.compare_profile_with_state(self.target_custom_profile, state)
        
        self.assertFalse(
            diff.subsystems["rgb_global"].has_changes,
            f"Expected rgb_global to be equivalent, got diff: {diff.subsystems['rgb_global'].summary}"
        )
        self.assertFalse(
            diff.subsystems["rgb_matrix"].has_changes,
            "Expected rgb_matrix to be identical"
        )
        self.assertFalse(diff.has_changes)

    def test_custom_target_0x80_readback_0x15_passes_if_matrix_exact(self):
        """Custom target 0x80 + readback 0x15 => passes, matrix exact."""
        state = self.base_state.clone()
        state.rgb_global = RGBGlobalConfig(
            effect=0x15,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )
        state.rgb_matrix_raw = bytearray(self.target_matrix_bytes)

        diff = self.pm.compare_profile_with_state(self.target_custom_profile, state)
        self.assertFalse(diff.subsystems["rgb_global"].has_changes)
        self.assertFalse(diff.subsystems["rgb_matrix"].has_changes)
        self.assertFalse(diff.has_changes)

    def test_custom_target_0x80_readback_0x14_with_wrong_matrix_fails(self):
        """Custom target 0x80 + readback 0x14 + wrong matrix => FAIL on rgb_matrix."""
        state = self.base_state.clone()
        state.rgb_global = RGBGlobalConfig(
            effect=0x14,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )
        # Alter slot 35 in readback matrix to black (0, 0, 0)
        wrong_matrix = RGBMatrix.from_raw(self.target_matrix_bytes)
        wrong_matrix.set_led(35, (0, 0, 0))
        state.rgb_matrix_raw = bytearray(wrong_matrix.to_raw())

        diff = self.pm.compare_profile_with_state(self.target_custom_profile, state)
        # Global is equivalent, but matrix must strictly fail
        self.assertFalse(diff.subsystems["rgb_global"].has_changes)
        self.assertTrue(diff.subsystems["rgb_matrix"].has_changes)
        self.assertTrue(diff.has_changes)
        self.assertIn("1 LED color(s) modified", diff.subsystems["rgb_matrix"].summary)

    def test_normal_target_0x0F_readback_0x14_fails(self):
        """Normal target 0x0F (Ripple Spread) + readback 0x14 => FAIL (no normalization)."""
        normal_profile = self.base_state.create_profile(1, name="Normal Profile Ripple")
        normal_profile.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,  # 0x0F
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )

        state = self.base_state.clone()
        state.rgb_global = RGBGlobalConfig(
            effect=0x14,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )

        diff = self.pm.compare_profile_with_state(normal_profile, state)
        self.assertTrue(diff.subsystems["rgb_global"].has_changes)
        # Verify that effect mismatch is reported
        effect_diffs = [d for d in diff.subsystems["rgb_global"].details if d.parameter == "effect"]
        self.assertEqual(len(effect_diffs), 1)
        self.assertEqual(effect_diffs[0].old_value, 0x14)
        self.assertEqual(effect_diffs[0].new_value, 0x0F)

    def test_custom_target_0x80_wrong_brightness_or_speed_fails(self):
        """Custom target 0x80 + wrong brightness/speed => FAIL."""
        # Test wrong brightness
        state_wrong_brightness = self.base_state.clone()
        state_wrong_brightness.rgb_global = RGBGlobalConfig(
            effect=0x14,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=3,                  # Expected 5
            speed=5,
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )
        state_wrong_brightness.rgb_matrix_raw = bytearray(self.target_matrix_bytes)

        diff = self.pm.compare_profile_with_state(self.target_custom_profile, state_wrong_brightness)
        self.assertTrue(diff.subsystems["rgb_global"].has_changes)
        param_names = [d.parameter for d in diff.subsystems["rgb_global"].details]
        self.assertIn("brightness", param_names)

        # Test wrong speed
        state_wrong_speed = self.base_state.clone()
        state_wrong_speed.rgb_global = RGBGlobalConfig(
            effect=0x14,
            primary=(255, 255, 255),
            secondary=(0, 0, 0),
            brightness=5,
            speed=2,                      # Expected 5
            color_mode=1,
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )
        state_wrong_speed.rgb_matrix_raw = bytearray(self.target_matrix_bytes)

        diff_speed = self.pm.compare_profile_with_state(self.target_custom_profile, state_wrong_speed)
        self.assertTrue(diff_speed.subsystems["rgb_global"].has_changes)
        param_names_spd = [d.parameter for d in diff_speed.subsystems["rgb_global"].details]
        self.assertIn("speed", param_names_spd)

    def test_parse_rgb_global_read_response_with_0x14_supported_without_catalog_entry(self):
        """parse_rgb_global_read_response() with 0x14 remains supported, not in RGB_EFFECT_CATALOG."""
        # 0x14 must NOT be in the catalog
        self.assertNotIn(0x14, RGB_EFFECT_CATALOG)
        self.assertNotIn(0x15, RGB_EFFECT_CATALOG)
        self.assertEqual(len(RGB_EFFECT_CATALOG), 26)

        # But 0x14 and 0x15 must be in EFFECT_CUSTOM_READBACK_ALIASES
        self.assertIn(0x14, EFFECT_CUSTOM_READBACK_ALIASES)
        self.assertIn(0x15, EFFECT_CUSTOM_READBACK_ALIASES)
        self.assertIn(0x80, EFFECT_CUSTOM_READBACK_ALIASES)

        # Helper function behaves correctly
        self.assertTrue(is_custom_effect_readback_equivalent(EFFECT_CUSTOM, 0x14))
        self.assertTrue(is_custom_effect_readback_equivalent(EFFECT_CUSTOM, 0x15))
        self.assertTrue(is_custom_effect_readback_equivalent(EFFECT_CUSTOM, 0x80))
        self.assertFalse(is_custom_effect_readback_equivalent(EFFECT_RIPPLE_SPREAD, 0x14))

        # Parsing real hardware response with 0x14
        raw_rep = bytes.fromhex(
            "551310000000010014ffffff00000000010505000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        )
        parsed = parse_rgb_global_read_response(raw_rep)
        self.assertEqual(parsed.effect, 0x14)
        self.assertEqual(parsed.primary, (255, 255, 255))
        self.assertEqual(parsed.color_mode, 1)
        self.assertEqual(parsed.brightness, 5)
        self.assertEqual(parsed.speed, 5)

    def test_effect_0x0f_quirk_metadata(self):
        """Verify Effect 0x0F capability/quirk metadata declarations."""
        self.assertIn(EFFECT_RIPPLE_SPREAD, EFFECTS_WITH_FIXED_RAINBOW_READBACK)
        self.assertTrue(is_effect_fixed_rainbow_readback(EFFECT_RIPPLE_SPREAD))
        self.assertFalse(is_effect_fixed_rainbow_readback(EFFECT_STATIC))

        meta_ripple = RGB_EFFECT_CATALOG[EFFECT_RIPPLE_SPREAD]
        self.assertTrue(meta_ripple.firmware_fixed_rainbow_readback)

        meta_static = RGB_EFFECT_CATALOG[EFFECT_STATIC]
        self.assertFalse(meta_static.firmware_fixed_rainbow_readback)

    def test_ripple_spread_0x0f_readback_ignores_primary_and_color_mode(self):
        """
        Firmware Quirk: For Effect 0x0F (Ripple Spread), hardware physically ignores
        primary_color and color_mode, always returning primary=(255,255,255) and color_mode=1.
        Post-write readback verification (is_readback=True) must NOT report a mismatch.
        """
        target_prof = self.base_state.create_profile(1, name="Target Ripple Red")
        target_prof.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(255, 0, 0),         # Pure Red
            secondary=(0, 0, 0),
            brightness=5,
            speed=4,
            color_mode=0,                # Single Color
            direction=0,
            effect_mode_type=0,
            driver_setting=255,
        )

        # Device returns real physical hardware readback:
        readback_state = self.base_state.clone()
        readback_state.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(255, 255, 255),     # Firmware always reports #FFFFFF
            secondary=(0, 0, 0),
            brightness=5,
            speed=4,
            color_mode=1,                # Firmware always reports Rainbow (1)
            direction=0,
            effect_mode_type=0,
            driver_setting=0,
        )

        # 1. Readback verification (is_readback=True) -> Must PASS cleanly with 0 diffs
        diff_readback = self.pm.compare_profile_with_state(target_prof, readback_state, is_readback=True)
        sub_rb = diff_readback.get_subsystem("rgb_global")
        self.assertFalse(
            sub_rb.has_changes,
            f"Expected readback verification to pass cleanly, but got diffs: {[d.parameter for d in sub_rb.details]}",
        )

        # 2. Offline planning diff (is_readback=False) -> Must detect changes so user can save/write
        diff_offline = self.pm.compare_profile_with_state(target_prof, readback_state, is_readback=False)
        sub_off = diff_offline.get_subsystem("rgb_global")
        self.assertTrue(sub_off.has_changes)
        param_names = [d.parameter for d in sub_off.details]
        self.assertIn("primary_color", param_names)
        self.assertIn("color_mode", param_names)

    def test_ripple_spread_0x0f_readback_still_strictly_verifies_brightness_and_speed(self):
        """Effect 0x0F quirk does NOT bypass verification for brightness, speed, or direction."""
        target_prof = self.base_state.create_profile(1, name="Target Ripple")
        target_prof.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(255, 0, 0),
            brightness=5,
            speed=4,
            color_mode=0,
        )

        # Device readback has wrong speed (2 instead of 4)
        readback_wrong_speed = self.base_state.clone()
        readback_wrong_speed.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(255, 255, 255),
            brightness=5,
            speed=2,                     # Wrong speed!
            color_mode=1,
        )

        diff = self.pm.compare_profile_with_state(target_prof, readback_wrong_speed, is_readback=True)
        sub = diff.get_subsystem("rgb_global")
        self.assertTrue(sub.has_changes)
        param_names = [d.parameter for d in sub.details]
        self.assertIn("speed", param_names)
        self.assertNotIn("primary_color", param_names)
        self.assertNotIn("color_mode", param_names)

    def test_other_effects_strictly_verify_primary_and_color_mode(self):
        """Non-quirked effects (e.g. Static 0x01) STILL strictly verify primary_color and color_mode."""
        target_prof = self.base_state.create_profile(1, name="Target Static Red")
        target_prof.rgb_global = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),
            brightness=5,
            speed=3,
            color_mode=0,
        )

        readback_wrong_color = self.base_state.clone()
        readback_wrong_color.rgb_global = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 255, 255),
            brightness=5,
            speed=3,
            color_mode=1,
        )

        # Even with is_readback=True, Static mode MUST fail verification
        diff = self.pm.compare_profile_with_state(target_prof, readback_wrong_color, is_readback=True)
        sub = diff.get_subsystem("rgb_global")
        self.assertTrue(sub.has_changes)
        param_names = [d.parameter for d in sub.details]
        self.assertIn("primary_color", param_names)
        self.assertIn("color_mode", param_names)


if __name__ == "__main__":
    unittest.main()
