"""
Unit tests for KeyConfig, KEY_MAP, address calculation, and real captures.
"""

import unittest
from pathlib import Path

from keyboard_re.assembler import load_image_from_file_or_capture
from keyboard_re.analyzer import diff_key_configs
from keyboard_re.models import (
    KEY_MAP,
    KeyConfig,
    ConfigImage,
    address_to_bank_col,
    key_address,
    mm_to_units,
    resolve_key_name,
    units_to_mm,
)


class TestKeyConfigModel(unittest.TestCase):
    """Tests for KeyConfig data model and units conversion."""

    def test_mm_to_units_and_back(self):
        self.assertEqual(mm_to_units(1.40), 140)
        self.assertEqual(mm_to_units(0.20), 20)
        self.assertEqual(mm_to_units(0.10), 10)
        self.assertEqual(mm_to_units(4.00), 400)
        self.assertEqual(mm_to_units(0.00), 0)

        self.assertEqual(units_to_mm(140), 1.40)
        self.assertEqual(units_to_mm(20), 0.20)
        self.assertEqual(units_to_mm(10), 0.10)
        self.assertEqual(units_to_mm(400), 4.00)
        self.assertEqual(units_to_mm(0), 0.0)

    def test_key_config_defaults(self):
        cfg = KeyConfig()
        self.assertEqual(cfg.actuation, 140)
        self.assertEqual(cfg.actuation_mm, 1.40)
        self.assertEqual(cfg.rt_press, 0)
        self.assertEqual(cfg.rt_press_mm, 0.0)
        self.assertEqual(cfg.rt_release, 0)
        self.assertEqual(cfg.rt_release_mm, 0.0)
        self.assertEqual(cfg.flags, 0)
        self.assertFalse(cfg.is_rt_enabled)

    def test_rt_off_representation(self):
        cfg = KeyConfig(actuation=140, rt_press=20, rt_release=10, flags=1)
        self.assertTrue(cfg.is_rt_enabled)

        cfg.disable_rt()
        self.assertFalse(cfg.is_rt_enabled)
        self.assertEqual(cfg.flags & 0x01, 0)

    def test_to_and_from_bytes(self):
        cfg = KeyConfig(actuation=140, rt_press=20, rt_release=10, flags=1, axis_type=1)
        raw = cfg.to_bytes()
        self.assertEqual(len(raw), 8)
        self.assertEqual(raw, bytes([0x01, 0x01, 0x8C, 0x00, 0x14, 0x00, 0x0A, 0x00]))

        restored = KeyConfig.from_bytes(raw)
        self.assertEqual(restored, cfg)


class TestKeyMatrixAddressCalculation(unittest.TestCase):
    """Tests for dense (bank * 16 + col) * 8 formula and priority keys."""

    def test_priority_keys_addresses(self):
        expected_coords = {
            "Q": (2, 1, 264, 0x0108),
            "A": (3, 1, 392, 0x0188),
            "S": (3, 2, 400, 0x0190),
            "D": (3, 3, 408, 0x0198),
            "F": (3, 4, 416, 0x01A0),
            "Z": (4, 1, 520, 0x0208),
            "ENTER": (4, 12, 608, 0x0260),
            "SPACE": (5, 3, 664, 0x0298),
            "LEFT": (5, 8, 704, 0x02C0),
            "DOWN": (5, 9, 712, 0x02C8),
            "UP": (5, 10, 720, 0x02D0),
            "RIGHT": (5, 11, 728, 0x02D8),
        }

        for key, (bank, col, exp_addr, exp_hex) in expected_coords.items():
            self.assertEqual(KEY_MAP[key], (bank, col), f"Key {key} matrix coords mismatch")
            addr = key_address(bank, col)
            self.assertEqual(addr, exp_addr, f"Key {key} address mismatch")
            self.assertEqual(addr, exp_hex, f"Key {key} hex address mismatch")
            # Reverse calculation
            rev = address_to_bank_col(addr)
            self.assertEqual(rev, (bank, col), f"Reverse address lookup failed for {key}")

    def test_all_84_keys_unique_addresses(self):
        self.assertEqual(len(KEY_MAP), 84, "Total mapped keys must be exactly 84")
        addrs = [key_address(b, c) for b, c in KEY_MAP.values()]
        self.assertEqual(len(set(addrs)), 84, "All 84 keys must map to unique addresses")
        for addr in addrs:
            self.assertTrue(0 <= addr < 1008, f"Address {addr} must be within 1008 bytes")


class TestPriorityKeysWithRealCaptures(unittest.TestCase):
    """Tests verifying S, A, D, F, Q, Z, Enter, Space and arrows against real capture files."""

    @classmethod
    def setUpClass(cls):
        cls.baseline_p = Path("captures/samples/baseline_140mm.json")
        cls.rt_press_p = Path("captures/experiments/key_s_rt_press.json")
        cls.rt_release_p = Path("captures/experiments/key_s_rt_release.json")
        cls.rt_toggle_p = Path("captures/experiments/key_s_rt_toggle.json")
        cls.up_p = Path("captures/experiments/key_up_139mm.json")
        cls.backslash_p = Path("captures/experiments/key_backslash_139mm.json")
        cls.backspace_p = Path("captures/experiments/key_backspace_139mm.json")

    def test_baseline_priority_keys(self):
        if not self.baseline_p.exists():
            self.skipTest("baseline_140mm.json not found")

        img = load_image_from_file_or_capture(self.baseline_p)
        priority_keys = ["S", "A", "D", "F", "Q", "Z", "ENTER", "SPACE", "LEFT", "DOWN", "UP", "RIGHT"]

        for key in priority_keys:
            cfg = img.get_key_config(key)
            self.assertEqual(cfg.actuation, 140, f"Key {key} actuation in baseline must be 1.40mm")
            self.assertEqual(cfg.actuation_mm, 1.40)
            self.assertEqual(cfg.rt_press, 0, f"Key {key} RT press in baseline must be 0")
            self.assertEqual(cfg.rt_release, 0, f"Key {key} RT release in baseline must be 0")
            self.assertFalse(cfg.is_rt_enabled)

    def test_key_s_rt_press_capture(self):
        if not self.rt_press_p.exists():
            self.skipTest("key_s_rt_press.json not found")

        img = load_image_from_file_or_capture(self.rt_press_p, session_index=2)
        s_cfg = img.get_key_config("S")
        self.assertEqual(s_cfg.actuation_mm, 1.40)
        self.assertEqual(s_cfg.rt_press_mm, 0.20)
        self.assertEqual(s_cfg.rt_release_mm, 0.20)
        self.assertFalse(s_cfg.is_rt_enabled)

        # Other keys must remain untouched in baseline state
        for key in ["A", "D", "F", "Q", "Z", "ENTER", "SPACE", "LEFT", "DOWN", "UP", "RIGHT"]:
            cfg = img.get_key_config(key)
            self.assertEqual(cfg.actuation_mm, 1.40)
            self.assertFalse(cfg.is_rt_enabled, f"Key {key} must not have RT enabled")

    def test_key_s_rt_release_capture(self):
        if not self.rt_release_p.exists():
            self.skipTest("key_s_rt_release.json not found")

        img = load_image_from_file_or_capture(self.rt_release_p)
        s_cfg = img.get_key_config("S")
        self.assertEqual(s_cfg.actuation_mm, 1.40)
        self.assertEqual(s_cfg.rt_press_mm, 0.20)
        self.assertEqual(s_cfg.rt_release_mm, 0.10)
        self.assertFalse(s_cfg.is_rt_enabled)

    def test_key_s_rt_toggle_capture(self):
        if not self.rt_toggle_p.exists():
            self.skipTest("key_s_rt_toggle.json not found")

        img = load_image_from_file_or_capture(self.rt_toggle_p)
        s_cfg = img.get_key_config("S")
        self.assertEqual(s_cfg.actuation_mm, 1.40)
        self.assertEqual(s_cfg.rt_press, 0, "RT toggle off must set rt_press to 0")
        self.assertEqual(s_cfg.rt_release, 0, "RT toggle off must set rt_release to 0")
        self.assertFalse(s_cfg.is_rt_enabled)

    def test_diff_key_configs_semantic(self):
        if not (self.rt_press_p.exists() and self.rt_release_p.exists()):
            self.skipTest("captures not found")

        press_img = load_image_from_file_or_capture(self.rt_press_p, session_index=2)
        rel_img = load_image_from_file_or_capture(self.rt_release_p)

        key_diffs = diff_key_configs(press_img, rel_img)
        self.assertEqual(len(key_diffs), 1, "Only Key S should change between press and release captures")
        diff = key_diffs[0]
        self.assertEqual(diff.key_name, "S")
        self.assertFalse(diff.actuation_changed)
        self.assertFalse(diff.rt_press_changed)
        self.assertTrue(diff.rt_release_changed)
        self.assertEqual(diff.old_config.rt_release_mm, 0.20)
        self.assertEqual(diff.new_config.rt_release_mm, 0.10)

    def test_key_up_capture(self):
        if not self.up_p.exists():
            self.skipTest("key_up_139mm.json not found")
        img = load_image_from_file_or_capture(self.up_p)
        cfg = img.get_key_config_by_bank_col(5, 10)
        self.assertEqual(cfg.actuation, 139, "Up arrow (Bank 5, Col 10) must be 1.39mm")
        self.assertEqual(cfg.actuation_mm, 1.39)

    def test_key_backslash_capture(self):
        if not self.backslash_p.exists():
            self.skipTest("key_backslash_139mm.json not found")
        img = load_image_from_file_or_capture(self.backslash_p)
        cfg = img.get_key_config_by_bank_col(3, 12)
        self.assertEqual(cfg.actuation, 139, "Backslash (Bank 3, Col 12) must be 1.39mm")
        self.assertEqual(cfg.actuation_mm, 1.39)

    def test_key_backspace_capture(self):
        if not self.backspace_p.exists():
            self.skipTest("key_backspace_139mm.json not found")
        img = load_image_from_file_or_capture(self.backspace_p)
        cfg = img.get_key_config_by_bank_col(5, 12)
        self.assertEqual(cfg.actuation, 139, "Backspace (Bank 5, Col 12) must be 1.39mm")
        self.assertEqual(cfg.actuation_mm, 1.39)



if __name__ == "__main__":
    unittest.main()
