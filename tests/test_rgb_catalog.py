"""
Unit tests for RGB Effect Catalog, protocol encoding/decoding, parameter validation,
profile diffing, and write plan coordination.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Dict, Tuple
import unittest

from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ParamDiff, ProfileDiff, ProfileManager
from keyboard_re.protocol import (
    EFFECT_BOUNCE_RIPPLE,
    EFFECT_BREATHING,
    EFFECT_CUSTOM,
    EFFECT_GIF,
    EFFECT_HEARTBEAT,
    EFFECT_OFF,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    LED_BUFFER_SIZE,
    RGB_EFFECT_CATALOG,
    RGB_GLOBAL_MAGIC,
    RGB_GLOBAL_PREFIX,
    RGB_PER_KEY_CHUNK_COUNT,
    RGB_PER_KEY_CHUNK_PREFIX,
    RGB_PER_KEY_TAIL_PREFIX,
    RGBEffectMeta,
    RGBGlobalConfig,
    build_led_buffer,
    build_rgb_global,
    build_rgb_per_key_chunks,
    get_effect_meta,
    parse_led_buffer,
    parse_rgb_global,
)
from keyboard_re.protocol.plan import ProfileWritePlan, build_profile_write_plan


class TestRGBCatalog(unittest.TestCase):
    """Test suite for the 26-mode RGB Catalog and metadata."""

    def test_catalog_completeness(self):
        """Verify all 26 confirmed effect modes are present in the catalog."""
        self.assertEqual(len(RGB_EFFECT_CATALOG), 26)
        expected_ids = {
            0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
            0x10, 0x11, 0x12, 0x13, 0x17, 0x18, 0x19,
            0x80, 0xFE, 0xFF,
        }
        self.assertEqual(set(RGB_EFFECT_CATALOG.keys()), expected_ids)

    def test_get_effect_meta(self):
        """Test get_effect_meta helper with valid and invalid IDs."""
        meta_static = get_effect_meta(0x01)
        self.assertIsNotNone(meta_static)
        self.assertEqual(meta_static.name_en, "Static Always On")
        self.assertEqual(meta_static.name_ru, "Статичное свечение")
        self.assertEqual(meta_static.category, "standard")
        self.assertTrue(meta_static.has_color)
        self.assertTrue(meta_static.has_color_mode)
        self.assertFalse(meta_static.has_speed)
        self.assertFalse(meta_static.has_direction)

        # Invalid IDs
        self.assertIsNone(get_effect_meta(0x14))
        self.assertIsNone(get_effect_meta(0x50))
        self.assertIsNone(get_effect_meta(0x99))

    def test_catalog_metadata_categories_and_flags(self):
        """Verify special, parametric, and directional metadata flags."""
        # 0x00 Off
        meta_off = get_effect_meta(0x00)
        self.assertEqual(meta_off.category, "special")
        self.assertTrue(meta_off.is_special_mode)

        # 0x80 Custom
        meta_custom = get_effect_meta(0x80)
        self.assertEqual(meta_custom.category, "special")
        self.assertTrue(meta_custom.is_special_mode)

        # 0xFE Heartbeat
        meta_hb = get_effect_meta(0xFE)
        self.assertEqual(meta_hb.category, "parametric")
        self.assertTrue(meta_hb.has_secondary_color)
        self.assertEqual(len(meta_hb.sub_modes), 4)

        # 0xFF GIF
        meta_gif = get_effect_meta(0xFF)
        self.assertEqual(meta_gif.category, "special")
        self.assertTrue(meta_gif.is_special_mode)

        # Directional modes: Wave (0x0B)
        meta_wave = get_effect_meta(0x0B)
        self.assertTrue(meta_wave.has_direction)
        self.assertEqual(meta_wave.direction_position, "left")


class TestRGBGlobalProtocol(unittest.TestCase):
    """Test suite for building and parsing AA 23 Global RGB reports."""

    def test_build_and_parse_standard_single_color(self):
        """Build and parse a standard Single Color mode (color_mode=0)."""
        pkt = build_rgb_global(
            effect=EFFECT_STATIC,
            primary=(255, 0, 128),
            brightness=4,
            speed=3,
            color_mode=0,
            direction=0,
        )
        self.assertEqual(len(pkt), 64)
        self.assertTrue(pkt.startswith(RGB_GLOBAL_PREFIX))
        self.assertEqual(pkt[8], 0x01)
        self.assertEqual(pkt[9:12], bytes([255, 0, 128]))
        self.assertEqual(pkt[12], 255)  # Host driver setting
        self.assertEqual(pkt[16], 0)    # Color mode: Single Color
        self.assertEqual(pkt[17], 4)    # Brightness
        self.assertEqual(pkt[18], 3)    # Speed
        self.assertEqual(pkt[19], 0)    # Direction
        self.assertEqual(pkt[22:24], RGB_GLOBAL_MAGIC)

        cfg = parse_rgb_global(pkt)
        self.assertEqual(cfg.effect, EFFECT_STATIC)
        self.assertEqual(cfg.primary, (255, 0, 128))
        self.assertEqual(cfg.secondary, (0, 0, 0))
        self.assertEqual(cfg.color_mode, 0)
        self.assertEqual(cfg.brightness, 4)
        self.assertEqual(cfg.speed, 3)
        self.assertEqual(cfg.direction, 0)
        self.assertEqual(cfg.driver_setting, 255)
        self.assertFalse(cfg.is_custom_mode)

    def test_build_and_parse_rainbow_rgb_mode(self):
        """Build and parse mode with color_mode=1 (Rainbow RGB) and direction=1."""
        pkt = build_rgb_global(
            effect=0x0B,  # Wave
            primary=(0, 255, 0),
            brightness=5,
            speed=2,
            color_mode=1,
            direction=1,
        )
        self.assertEqual(pkt[16], 1)
        self.assertEqual(pkt[19], 1)

        cfg = parse_rgb_global(pkt)
        self.assertEqual(cfg.effect, 0x0B)
        self.assertEqual(cfg.color_mode, 1)
        self.assertEqual(cfg.direction, 1)

    def test_build_and_parse_heartbeat_mode(self):
        """Build and parse 0xFE Heartbeat with secondary color and sub-mode."""
        pkt = build_rgb_global(
            effect=EFFECT_HEARTBEAT,
            primary=(255, 0, 0),
            secondary=(0, 0, 255),
            brightness=3,
            speed=4,
            color_mode=0,
            effect_mode_type=2,  # both_breathing
        )
        self.assertEqual(pkt[8], 0xFE)
        self.assertEqual(pkt[9:12], bytes([255, 0, 0]))
        self.assertEqual(pkt[13:16], bytes([0, 0, 255]))
        self.assertEqual(pkt[20], 2)

        cfg = parse_rgb_global(pkt)
        self.assertEqual(cfg.effect, EFFECT_HEARTBEAT)
        self.assertEqual(cfg.primary, (255, 0, 0))
        self.assertEqual(cfg.secondary, (0, 0, 255))
        self.assertEqual(cfg.effect_mode_type, 2)
        self.assertEqual(cfg.meta.name_en, "Heartbeat")

    def test_custom_mode_property(self):
        """Verify is_custom_mode property is True for 0x80."""
        pkt = build_rgb_global(effect=EFFECT_CUSTOM, primary=(255, 255, 255))
        cfg = parse_rgb_global(pkt)
        self.assertTrue(cfg.is_custom_mode)

    def test_validation_errors(self):
        """Verify ValueError is raised on invalid parameters."""
        # Effect out of range
        with self.assertRaises(ValueError):
            build_rgb_global(effect=-1, primary=(0, 0, 0))
        with self.assertRaises(ValueError):
            build_rgb_global(effect=256, primary=(0, 0, 0))

        # Brightness out of range [0, 5]
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), brightness=-1)
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), brightness=6)

        # Speed out of range [1, 5]
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), speed=0)
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), speed=6)

        # Primary color invalid
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0))  # only 2 elements
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 256))

        # Direction out of range
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), direction=256)

        # Parse invalid reports
        with self.assertRaises(ValueError):
            parse_rgb_global(bytes(63))  # Wrong length
        with self.assertRaises(ValueError):
            parse_rgb_global(b"\x00" * 64)  # Wrong prefix

    def test_to_from_dict(self):
        """Verify serialization round-trip via to_dict() and from_dict()."""
        cfg = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(100, 150, 200),
            secondary=(10, 20, 30),
            brightness=2,
            speed=4,
            color_mode=0,
            direction=1,
            effect_mode_type=0,
            driver_setting=255,
        )
        d = cfg.to_dict()
        self.assertEqual(d["effect"], EFFECT_RIPPLE_SPREAD)
        self.assertEqual(d["effect_name"], "Ripple Spread")
        self.assertEqual(d["color_mode"], 0)
        self.assertEqual(d["direction"], 1)

        reconstructed = RGBGlobalConfig.from_dict(d)
        self.assertEqual(reconstructed.effect, cfg.effect)
        self.assertEqual(reconstructed.primary, cfg.primary)
        self.assertEqual(reconstructed.secondary, cfg.secondary)
        self.assertEqual(reconstructed.color_mode, cfg.color_mode)
        self.assertEqual(reconstructed.direction, cfg.direction)
        self.assertEqual(reconstructed.brightness, cfg.brightness)
        self.assertEqual(reconstructed.speed, cfg.speed)


class TestPerKeyRGBProtocol(unittest.TestCase):
    """Test suite for Per-Key RGB buffer and chunk builders."""

    def test_build_and_parse_led_buffer(self):
        """Test building and parsing 512-byte Per-Key LED buffer."""
        colors = {
            0: (255, 0, 0),
            1: (0, 255, 0),
            2: (0, 0, 255),
        }
        buf = build_led_buffer(colors)
        self.assertEqual(len(buf), LED_BUFFER_SIZE)

        # Slot 0: R=255, G=0, B=0, ID=0
        self.assertEqual(buf[0:4], bytes([255, 0, 0, 0]))
        # Slot 1: R=0, G=255, B=0, ID=1
        self.assertEqual(buf[4:8], bytes([0, 255, 0, 1]))
        # Slot 2: R=0, G=0, B=255, ID=2
        self.assertEqual(buf[8:12], bytes([0, 0, 255, 2]))

        parsed = parse_led_buffer(buf)
        self.assertEqual(parsed[0], (255, 0, 0))
        self.assertEqual(parsed[1], (0, 255, 0))
        self.assertEqual(parsed[2], (0, 0, 255))
        self.assertEqual(parsed[3], (0, 0, 0))

    def test_per_key_chunks(self):
        """Test generating 10 HID output reports for Per-Key matrix."""
        buf = bytes(LED_BUFFER_SIZE)
        chunks = build_rgb_per_key_chunks(buf)
        self.assertEqual(len(chunks), 10)

        # First 9 are standard chunks with prefix AA 24 38
        for i in range(9):
            self.assertEqual(len(chunks[i]), 64)
            self.assertTrue(chunks[i].startswith(RGB_PER_KEY_CHUNK_PREFIX))

        # Last chunk is tail with prefix AA 24 08
        self.assertEqual(len(chunks[9]), 64)
        self.assertTrue(chunks[9].startswith(RGB_PER_KEY_TAIL_PREFIX))


class TestProfileIntegrationRGB(unittest.TestCase):
    """Test suite for Profile diffing, state model, and write plan coordination."""

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

    def test_device_state_to_profile_rgb_roundtrip(self):
        """Verify Profile with extended RGB converts to dict and back with exact buffers."""
        profile = self.manager.create_profile_from_state(
            self.state,
            profile_id=10,
            name="Heartbeat Profile",
        )
        profile.rgb_global = RGBGlobalConfig(
            effect=EFFECT_HEARTBEAT,
            primary=(255, 128, 0),
            secondary=(0, 128, 255),
            brightness=4,
            speed=2,
            color_mode=0,
            direction=0,
            effect_mode_type=1,
            driver_setting=255,
        )

        prof_dict = profile.to_dict()
        reconstructed_prof = Profile.from_dict(prof_dict)
        self.assertEqual(reconstructed_prof.rgb_global.effect, EFFECT_HEARTBEAT)
        self.assertEqual(reconstructed_prof.rgb_global.secondary, (0, 128, 255))
        self.assertEqual(reconstructed_prof.rgb_global.effect_mode_type, 1)
        self.assertEqual(reconstructed_prof.rgb_global.color_mode, 0)
        self.assertEqual(reconstructed_prof.rgb_global.brightness, 4)

        orig_bufs = profile.dump_raw_buffers()
        reconstructed_bufs = reconstructed_prof.dump_raw_buffers()
        self.assertEqual(orig_bufs["rgb_global"], reconstructed_bufs["rgb_global"])

    def test_profile_diff_detects_extended_rgb_changes(self):
        """Verify ProfileDiff detects changes to color_mode, direction, secondary, and effect_mode_type."""
        # 1. Change color_mode: 0 -> 1
        prof1 = self.manager.create_profile_from_state(self.state, profile_id=1, name="P1")
        prof1.rgb_global.color_mode ^= 1
        diff1 = self.manager.compare_profile_with_state(prof1, self.state)
        self.assertTrue(diff1.has_changes)
        self.assertIn("rgb_global", diff1.changed_subsystems)
        sub1 = diff1.subsystems["rgb_global"]
        param_names1 = [d.parameter for d in sub1.details if isinstance(d, ParamDiff)]
        self.assertIn("color_mode", param_names1)

        # 2. Change direction: 0 -> 1
        prof2 = self.manager.create_profile_from_state(self.state, profile_id=2, name="P2")
        prof2.rgb_global.direction ^= 1
        diff2 = self.manager.compare_profile_with_state(prof2, self.state)
        self.assertTrue(diff2.has_changes)
        sub2 = diff2.subsystems["rgb_global"]
        param_names2 = [d.parameter for d in sub2.details if isinstance(d, ParamDiff)]
        self.assertIn("direction", param_names2)

        # 3. Change secondary color
        prof3 = self.manager.create_profile_from_state(self.state, profile_id=3, name="P3")
        prof3.rgb_global.secondary = (123, 45, 67)
        diff3 = self.manager.compare_profile_with_state(prof3, self.state)
        self.assertTrue(diff3.has_changes)
        sub3 = diff3.subsystems["rgb_global"]
        param_names3 = [d.parameter for d in sub3.details if isinstance(d, ParamDiff)]
        self.assertIn("secondary_color", param_names3)

        # 4. Change effect_mode_type
        prof4 = self.manager.create_profile_from_state(self.state, profile_id=4, name="P4")
        prof4.rgb_global.effect_mode_type = 3
        diff4 = self.manager.compare_profile_with_state(prof4, self.state)
        self.assertTrue(diff4.has_changes)
        sub4 = diff4.subsystems["rgb_global"]
        param_names4 = [d.parameter for d in sub4.details if isinstance(d, ParamDiff)]
        self.assertIn("effect_mode_type", param_names4)

        # 5. No changes -> has_changes is False
        prof_same = self.manager.create_profile_from_state(self.state, profile_id=5, name="Same")
        diff_same = self.manager.compare_profile_with_state(prof_same, self.state)
        self.assertFalse(diff_same.has_changes)

    def test_write_plan_for_rgb_global(self):
        """Verify ProfileWritePlan builds a valid AA 23 10 chunk with expected ACK."""
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="New RGB")
        profile.rgb_global = RGBGlobalConfig(
            effect=EFFECT_RIPPLE_SPREAD,
            primary=(0, 255, 255),
            brightness=2,
            speed=4,
            color_mode=0,
        )
        plan = build_profile_write_plan(self.state, profile)
        self.assertFalse(plan.is_empty)
        step = plan.get_step("rgb_global")
        self.assertIsNotNone(step)
        self.assertEqual(step.opcode, 0x23)
        self.assertEqual(step.packet_count, 1)

        chunk = step.chunks[0]
        self.assertTrue(chunk.packet.startswith(RGB_GLOBAL_PREFIX))
        self.assertEqual(chunk.packet[8], EFFECT_RIPPLE_SPREAD)
        self.assertEqual(chunk.expected_ack[:3], b"\x55\x23\x10")

    def test_custom_mode_write_plan_coordination(self):
        """
        Verify Custom mode (0x80) coordination:
        When activating 0x80, ProfileWritePlan ensures both AA 23 10 and AA 24 chunks are planned.
        """
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Custom RGB")
        profile.rgb_global.effect = EFFECT_CUSTOM

        plan = build_profile_write_plan(self.state, profile)
        self.assertIn("rgb_global", plan.modified_subsystems)
        self.assertIn("rgb_matrix", plan.modified_subsystems)
        # 1 global packet + 10 per-key matrix packets = 11 packets
        self.assertEqual(plan.total_packets, 11)

    def test_rgb_global_reserved_mid_does_not_overwrite_color_mode_or_secondary(self):
        """
        [RGB Global Regression Test]
        Verify that build_rgb_global does not allow legacy reserved_mid to overwrite
        explicitly set color_mode or secondary color.
        """
        # Baseline state has rainbow mode (color_mode=1), so reserved_mid is b"\x00\x01"
        prof = self.manager.create_profile_from_state(self.state, profile_id=1, name="RGB Fix")
        self.assertEqual(prof.rgb_global.reserved_mid, b"\x00\x01")

        # User explicitly changes color_mode to 0 (Single Color) and secondary color
        prof.rgb_global.color_mode = 0
        prof.rgb_global.secondary = (12, 34, 56)

        plan = build_profile_write_plan(self.state, prof)
        step = plan.get_step("rgb_global")
        self.assertIsNotNone(step)
        pkt = step.chunks[0].packet

        # Byte 15 must be secondary Blue (56), NOT overwritten by reserved_mid[0] (0)
        self.assertEqual(pkt[15], 56, f"secondary[2] (byte 15) was overwritten! Got {pkt[15]}")
        # Byte 16 must be color_mode (0), NOT overwritten by reserved_mid[1] (1)
        self.assertEqual(pkt[16], 0, f"color_mode (byte 16) was overwritten! Got {pkt[16]}")

        # Parsing packet must faithfully recover user's settings
        parsed = parse_rgb_global(pkt)
        self.assertEqual(parsed.color_mode, 0)
        self.assertEqual(parsed.secondary, (12, 34, 56))

    def test_rgb_global_driver_setting_not_treated_as_mismatch(self):
        """
        [RGB Global Regression Test]
        driver_setting is a transport write flag (255) echoed as 0 by firmware on readback.
        Verify that _diff_rgb_global does not report driver_setting differences as mismatches.
        """
        target_cfg = copy.deepcopy(self.state.rgb_global)
        target_cfg.driver_setting = 255

        readback_cfg = copy.deepcopy(self.state.rgb_global)
        readback_cfg.driver_setting = 0

        diff = self.manager._diff_rgb_global(target_cfg, readback_cfg)
        self.assertFalse(
            diff.has_changes,
            f"Expected Identical, but got: {diff.summary} {[d.parameter for d in diff.details]}",
        )

    def test_rgb_global_apply_closed_loop_two_parameter_mismatch_resolved(self):
        """
        [RGB Global Regression Test - Root Cause Verification]
        Reproduces the exact scenario of 'rgb_global: 2 parameter(s) modified':
        1. Profile has color_mode=0 (Single Color) and driver_setting=255.
        2. Firmware returns readback with driver_setting=0.
        Verifies that:
        - Packet wire format correctly transmits color_mode=0 (not corrupted to 1 by reserved_mid).
        - Readback comparison passes cleanly with 0 mismatches instead of 2.
        """
        prof = self.manager.create_profile_from_state(self.state, profile_id=1, name="RGB ABA")
        prof.rgb_global.color_mode = 0
        prof.rgb_global.driver_setting = 255

        plan = build_profile_write_plan(self.state, prof)
        step = plan.get_step("rgb_global")
        self.assertIsNotNone(step)
        pkt = step.chunks[0].packet

        # Verify wire packet carries color_mode=0
        self.assertEqual(pkt[16], 0)

        # Simulate device readback on AA 13 after write
        readback_rep = bytearray(pkt)
        readback_rep[0:3] = b"\x55\x13\x10"
        readback_rep[12] = 0x00  # Firmware internal status
        readback_rep[22:24] = b"\x00\x00"

        from keyboard_re.protocol.read import parse_rgb_global_read_response
        readback_cfg = parse_rgb_global_read_response(readback_rep)

        readback_state = copy.deepcopy(self.state)
        readback_state.rgb_global = readback_cfg

        diff = self.manager.compare_profile_with_state(prof, readback_state)
        sub = diff.get_subsystem("rgb_global")
        self.assertFalse(
            sub.has_changes,
            f"Expected 0 mismatches, but got: {sub.summary}: {[d.parameter for d in sub.details]}",
        )


if __name__ == "__main__":
    unittest.main()
