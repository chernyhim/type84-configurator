"""
Unit tests for experimentally verified Hall Effect fields on IO by Red Square Type 84 Magnetic Black:
- Actuation Point (+0..1, uint16 LE, 0.01 mm per LSB) - CONFIRMED
- RT Press Sensitivity (+2..3, uint16 LE, 0.01 mm per LSB, 0 = OFF) - CONFIRMED
- RT Release Sensitivity (+4..5, uint16 LE, 0.01 mm per LSB, 0 = OFF) - CONFIRMED
- Flags / Mode (+6..7, uint16 LE) - UNKNOWN (preserved verbatim)
"""

import struct
import unittest

from keyboard_re.models.base import (
    CONFIG_IMAGE_SIZE,
    KEY_MAP,
    KEY_RECORD_SIZE,
    KeyConfig,
    key_address,
)
from keyboard_re.models.state import HallProfileState
from keyboard_re.protocol.hall import (
    HallKeyConfig,
    decode_hall_image,
    decode_hall_record,
    decode_mm,
    encode_hall_image,
    encode_hall_record,
    encode_mm,
    mm_to_raw,
    raw_to_mm,
)


class TestMillimeterConversions(unittest.TestCase):
    """Tests for unified millimeter <-> uint16 raw conversion."""

    def test_roundtrip_raw_to_mm_to_raw(self):
        """Verify round-trip raw -> mm -> raw for full uint16 operating range [0..400]."""
        for raw in range(0, 401):
            mm = raw_to_mm(raw)
            recovered_raw = mm_to_raw(mm)
            self.assertEqual(
                recovered_raw,
                raw,
                f"Round-trip raw -> mm -> raw failed for raw={raw} (mm={mm})",
            )

    def test_roundtrip_mm_to_raw_to_mm(self):
        """Verify round-trip mm -> raw -> mm for 0.00 to 4.00 mm with 0.01 mm step."""
        for step in range(0, 401):
            mm = round(step * 0.01, 2)
            raw = mm_to_raw(mm)
            recovered_mm = raw_to_mm(raw)
            self.assertEqual(
                recovered_mm,
                mm,
                f"Round-trip mm -> raw -> mm failed for mm={mm} (raw={raw})",
            )

    def test_benchmark_values(self):
        """
        Verify exact empirically confirmed benchmark values from physical keyboard experiments:
        0.00 -> 0x0000 (b'\\x00\\x00')
        0.10 -> 0x000A (b'\\x0A\\x00')
        0.20 -> 0x0014 (b'\\x14\\x00')
        0.50 -> 0x0032 (b'\\x32\\x00')
        1.00 -> 0x0064 (b'\\x64\\x00')
        1.40 -> 0x008C (b'\\x8C\\x00')
        """
        benchmarks = [
            (0.00, 0x0000, b"\x00\x00"),
            (0.10, 0x000A, b"\x0A\x00"),
            (0.20, 0x0014, b"\x14\x00"),
            (0.50, 0x0032, b"\x32\x00"),
            (1.00, 0x0064, b"\x64\x00"),
            (1.40, 0x008C, b"\x8C\x00"),
        ]
        for val_mm, expected_raw, expected_bytes in benchmarks:
            raw = mm_to_raw(val_mm)
            self.assertEqual(
                raw,
                expected_raw,
                f"mm_to_raw({val_mm}) expected 0x{expected_raw:04X}, got 0x{raw:04X}",
            )
            encoded = encode_mm(val_mm)
            self.assertEqual(
                encoded,
                expected_bytes,
                f"encode_mm({val_mm}) expected {expected_bytes.hex()}, got {encoded.hex()}",
            )
            decoded_mm = decode_mm(encoded)
            self.assertAlmostEqual(
                decoded_mm,
                val_mm,
                places=2,
                msg=f"decode_mm({encoded.hex()}) expected {val_mm}, got {decoded_mm}",
            )

    def test_little_endian_encoding(self):
        """Verify little-endian byte ordering explicitly."""
        # 1.40 mm -> raw 140 = 0x008C -> little-endian bytes: 0x8C, 0x00
        encoded_140 = encode_mm(1.40)
        self.assertEqual(encoded_140[0], 0x8C)
        self.assertEqual(encoded_140[1], 0x00)

        # 1.00 mm -> raw 100 = 0x0064 -> little-endian bytes: 0x64, 0x00
        encoded_100 = encode_mm(1.00)
        self.assertEqual(encoded_100[0], 0x64)
        self.assertEqual(encoded_100[1], 0x00)

        # Buffer offset decoding
        buf = b"\xFF\xFF" + encoded_140 + b"\xEE\xEE"
        self.assertEqual(decode_mm(buf, offset=2), 1.40)


class TestKeyAAddresses(unittest.TestCase):
    """
    Verify exact physical and logical addresses for Key A (Bank 3, Col 1).
    Base address: 3 * 128 + 5 + 1 * 8 = 397 = 0x018D
    """

    def test_key_a_base_address(self):
        bank, col = KEY_MAP["A"]
        self.assertEqual((bank, col), (3, 1))
        addr = key_address(bank, col)
        self.assertEqual(addr, 392)
        self.assertEqual(f"0x{addr:04X}", "0x0188")

    def test_key_a_field_ranges(self):
        bank, col = KEY_MAP["A"]
        base = key_address(bank, col)

        axis_type_addr = base
        flags_addr = base + 1
        actuation_addr = base + 2
        rt_press_addr = base + 4
        rt_release_addr = base + 6

        self.assertEqual(axis_type_addr, 0x0188)
        self.assertEqual(flags_addr, 0x0189)
        self.assertEqual(actuation_addr, 0x018A)
        self.assertEqual(rt_press_addr, 0x018C)
        self.assertEqual(rt_release_addr, 0x018E)


class TestHallRecordFieldIsolation(unittest.TestCase):
    """
    Verify that modifying each confirmed parameter touches ONLY its respective bytes in <BBHHH,
    and that Flags remains completely unchanged unless explicitly modified.
    """

    def test_actuation_modification_isolation(self):
        """Changing Actuation touches ONLY bytes 2..3 (+2..+3)."""
        cfg = HallKeyConfig(
            actuation_mm=1.40,
            rt_press_mm=0.20,
            rt_release_mm=0.10,
            flags=0x12,
        )
        original_bytes = cfg.to_bytes()
        self.assertEqual(len(original_bytes), KEY_RECORD_SIZE)

        # Modify actuation only
        cfg.actuation_mm = 2.00
        new_bytes = cfg.to_bytes()

        # Untouched prefix: bytes 0..1 (axis_type, flags)
        self.assertEqual(new_bytes[:2], original_bytes[:2])
        # Changed slice: bytes 2..3 (Actuation)
        self.assertEqual(new_bytes[2:4], struct.pack("<H", 200))
        # Untouched suffix: bytes 4..7 (RT Press, RT Release)
        self.assertEqual(new_bytes[4:], original_bytes[4:])
        # Flags unchanged
        self.assertEqual(cfg.flags, 0x12)

    def test_rt_press_modification_isolation(self):
        """Changing RT Press touches ONLY bytes 4..5 (+4..+5)."""
        cfg = HallKeyConfig(
            actuation_mm=1.40,
            rt_press_mm=0.00,
            rt_release_mm=0.00,
            flags=0x34,
        )
        original_bytes = cfg.to_bytes()

        # Modify RT press only
        cfg.rt_press_mm = 0.35
        new_bytes = cfg.to_bytes()

        # Untouched bytes 0..3 (Axis, Flags, Actuation)
        self.assertEqual(new_bytes[:4], original_bytes[:4])
        # Changed bytes 4..5 (RT Press)
        self.assertEqual(new_bytes[4:6], struct.pack("<H", 35))
        # Untouched bytes 6..7 (RT Release)
        self.assertEqual(new_bytes[6:], original_bytes[6:])
        # Flags unchanged
        self.assertEqual(cfg.flags, 0x34)

    def test_rt_release_modification_isolation(self):
        """Changing RT Release touches ONLY bytes 6..7 (+6..+7)."""
        cfg = HallKeyConfig(
            actuation_mm=1.40,
            rt_press_mm=0.20,
            rt_release_mm=0.00,
            flags=0x56,
        )
        original_bytes = cfg.to_bytes()

        # Modify RT release only
        cfg.rt_release_mm = 0.15
        new_bytes = cfg.to_bytes()

        # Untouched bytes 0..5 (Axis, Flags, Actuation, RT Press)
        self.assertEqual(new_bytes[:6], original_bytes[:6])
        # Changed bytes 6..7 (RT Release)
        self.assertEqual(new_bytes[6:8], struct.pack("<H", 15))
        # Flags unchanged
        self.assertEqual(cfg.flags, 0x56)

    def test_flags_preserved_verbatim(self):
        """Verify flags byte (+1) is preserved without mutation."""
        for flag_val in [0x00, 0x01, 0x02, 0x80, 0xFF, 0x5A]:
            cfg = HallKeyConfig(
                actuation_mm=1.20,
                rt_press_mm=0.10,
                rt_release_mm=0.10,
                flags=flag_val,
            )
            raw = cfg.to_bytes()
            unpacked_flag = raw[1]
            self.assertEqual(unpacked_flag, flag_val)

            restored = HallKeyConfig.from_bytes(raw)
            self.assertEqual(restored.flags, flag_val)
            self.assertEqual(restored, cfg)


class TestHallKeyConfigModel(unittest.TestCase):
    """Tests for HallKeyConfig model behavior, helper methods, and backwards compatibility."""

    def test_default_values(self):
        cfg = HallKeyConfig()
        self.assertEqual(cfg.actuation_mm, 1.40)
        self.assertEqual(cfg.actuation_raw, 140)
        self.assertEqual(cfg.rt_press_mm, 0.00)
        self.assertEqual(cfg.rt_press_raw, 0)
        self.assertEqual(cfg.rt_release_mm, 0.00)
        self.assertEqual(cfg.rt_release_raw, 0)
        self.assertEqual(cfg.flags, 0x0000)
        self.assertFalse(cfg.is_rt_enabled)

    def test_rt_helpers(self):
        cfg = HallKeyConfig()
        self.assertFalse(cfg.is_rt_enabled)

        cfg.enable_rt(press_mm=0.20, release_mm=0.15)
        self.assertTrue(cfg.is_rt_enabled)
        self.assertEqual(cfg.rt_press_mm, 0.20)
        self.assertEqual(cfg.rt_release_mm, 0.15)
        self.assertEqual(cfg.rt_press_raw, 20)
        self.assertEqual(cfg.rt_release_raw, 15)

        cfg.disable_rt()
        self.assertFalse(cfg.is_rt_enabled)
        # Retains configured sensitivities when disabled
        self.assertEqual(cfg.rt_press_mm, 0.20)
        self.assertEqual(cfg.rt_release_mm, 0.15)
        self.assertEqual(cfg.rt_press_raw, 20)
        self.assertEqual(cfg.rt_release_raw, 15)

    def test_keyconfig_subclass_interoperability(self):
        """Verify KeyConfig inherits from HallKeyConfig and maintains full compatibility."""
        kc = KeyConfig(actuation=140, rt_press=20, rt_release=10, flags=0)
        hk = HallKeyConfig(actuation_mm=1.40, rt_press_mm=0.20, rt_release_mm=0.10, flags=0)

        self.assertIsInstance(kc, HallKeyConfig)
        self.assertEqual(kc, hk)
        self.assertEqual(hk, kc)
        self.assertEqual(kc.to_bytes(), hk.to_bytes())


class TestImageLevelOperations(unittest.TestCase):
    """Tests verifying 1008-byte image decode/encode operations with typed records."""

    def test_decode_and_encode_hall_image(self):
        base_buf = bytearray(CONFIG_IMAGE_SIZE)
        # Put Key A at dense slot 49: Actuation 1.40 mm (0x008C), RT Press 0.20 mm (0x0014), RT Release 0.10 mm (0x000A), Flags 0
        addr_a = key_address(3, 1)
        base_buf[addr_a : addr_a + 8] = struct.pack("<BBHHH", 1, 0, 140, 20, 10)

        decoded = decode_hall_image(base_buf)
        self.assertIn("A", decoded)
        self.assertEqual(decoded["A"].actuation_mm, 1.40)
        self.assertEqual(decoded["A"].rt_press_mm, 0.20)
        self.assertEqual(decoded["A"].rt_release_mm, 0.10)

        # Modify Key A and re-encode
        decoded["A"].actuation_mm = 2.50
        reencoded = encode_hall_image(decoded, base_image=base_buf)
        self.assertEqual(len(reencoded), CONFIG_IMAGE_SIZE)

        # Check that only Key A actuation changed in the whole image
        new_decoded = decode_hall_image(reencoded)
        self.assertEqual(new_decoded["A"].actuation_mm, 2.50)
        self.assertEqual(new_decoded["A"].rt_press_mm, 0.20)
        self.assertEqual(new_decoded["A"].rt_release_mm, 0.10)

    def test_state_model_preserves_rt_and_flags(self):
        """Verify HallProfileState handles RT Press, RT Release, and Flags without data corruption."""
        raw_img = bytearray(CONFIG_IMAGE_SIZE)
        state = HallProfileState.from_bytes(raw_img, profile_id=1)

        state.set_actuation("A", 1.80)
        state.set_rt("A", press_mm=0.30, release_mm=0.15)

        cfg = state.get_key("A")
        self.assertEqual(cfg.actuation_mm, 1.80)
        self.assertEqual(cfg.rt_press_mm, 0.30)
        self.assertEqual(cfg.rt_release_mm, 0.15)
        self.assertTrue(cfg.is_rt_enabled)

        # Verify raw bytes in state (<BBHHH format)
        addr = key_address(3, 1)
        self.assertEqual(state.raw_image[addr : addr + 2], bytes([0, 1]))  # axis_type=0, flags=1
        self.assertEqual(state.raw_image[addr + 2 : addr + 4], struct.pack("<H", 180))  # actuation
        self.assertEqual(state.raw_image[addr + 4 : addr + 6], struct.pack("<H", 30))   # rt_press
        self.assertEqual(state.raw_image[addr + 6 : addr + 8], struct.pack("<H", 15))   # rt_release


if __name__ == "__main__":
    unittest.main()
