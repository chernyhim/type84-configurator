"""
Unit and Golden tests for the offline protocol layer (keyboard_re.protocol).

Verifies:
1. Hall / AA 27 encoding, decoding, and round-trip against real captures.
2. Global RGB / AA 23 encoding, decoding, and round-trip against real captures.
3. Per-Key RGB / AA 24 encoding, decoding, and round-trip against real captures.
4. Physical LED layout mapping strictly decoupled from Hall switch KEY_MAP.
5. Exact byte-for-byte golden tests: encode(parse(real_capture)) == real_capture.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.models import KEY_MAP, RawCapture
from keyboard_re.protocol import (
    EFFECT_BREATHING,
    EFFECT_CUSTOM,
    EFFECT_STATIC,
    HALL_CHUNK_COUNT,
    HALL_CHUNK_PAYLOAD_SIZE,
    HALL_IMAGE_SIZE,
    LED_BUFFER_SIZE,
    LED_SLOT_COUNT,
    PHYSICAL_LED_MAP,
    REPORT_ID,
    REPORT_SIZE,
    RGB_GLOBAL_MAGIC,
    RGB_GLOBAL_PREFIX,
    RGB_PER_KEY_TOTAL_REPORTS,
    USAGE,
    USAGE_PAGE,
    build_hall_chunk,
    build_hall_terminator,
    build_hall_write,
    build_led_buffer,
    build_rgb_global,
    build_rgb_per_key_chunks,
    get_led_index,
    parse_hall_chunk,
    parse_hall_terminator,
    parse_hall_write,
    parse_led_buffer,
    parse_rgb_global,
    parse_rgb_per_key_chunks,
)


class TestProtocolConstants(unittest.TestCase):
    """Verify hardware wire metadata and dimensions."""

    def test_metadata_constants(self):
        self.assertEqual(REPORT_ID, 0)
        self.assertEqual(REPORT_SIZE, 64)
        self.assertEqual(USAGE_PAGE, 0xFF68)
        self.assertEqual(USAGE, 0x0061)
        self.assertEqual(HALL_IMAGE_SIZE, 1008)
        self.assertEqual(HALL_CHUNK_COUNT, 18)
        self.assertEqual(HALL_CHUNK_PAYLOAD_SIZE, 56)
        self.assertEqual(LED_BUFFER_SIZE, 512)
        self.assertEqual(LED_SLOT_COUNT, 128)
        self.assertEqual(RGB_PER_KEY_TOTAL_REPORTS, 10)


class TestHallProtocol(unittest.TestCase):
    """Test pure offline Hall effect (AA 27) encoder and decoder."""

    def test_build_and_parse_hall_chunk(self):
        payload = bytes([i % 256 for i in range(56)])
        report = build_hall_chunk(address=56, payload=payload)
        self.assertEqual(len(report), 64)
        self.assertTrue(report.startswith(b"\xAA\x27\x38\x38\x00"))
        self.assertEqual(report[5:8], b"\x00\x00\x00", "Header padding bytes 5..7 must be 3 zeroes")
        self.assertEqual(report[8:64], payload, "Payload must start at byte 8")

        parsed_addr, parsed_payload = parse_hall_chunk(report)
        self.assertEqual(parsed_addr, 56)
        self.assertEqual(parsed_payload, payload)

    def test_build_hall_chunk_invalid_args(self):
        with self.assertRaises(ValueError):
            build_hall_chunk(address=56, payload=b"\x00" * 55)  # wrong payload size
        with self.assertRaises(ValueError):
            build_hall_chunk(address=55, payload=b"\x00" * 56)  # unaligned address
        with self.assertRaises(ValueError):
            build_hall_chunk(address=1008, payload=b"\x00" * 56)  # out of bounds

    def test_build_and_parse_hall_write(self):
        img = bytes([i % 251 for i in range(1008)])
        chunks = build_hall_write(img)
        self.assertEqual(len(chunks), 18)
        for i, c in enumerate(chunks):
            self.assertEqual(len(c), 64)
            addr, pl = parse_hall_chunk(c)
            self.assertEqual(addr, i * 56)
            self.assertEqual(pl, img[addr : addr + 56])

        # Round-trip through reassembly
        reconstructed = parse_hall_write(chunks)
        self.assertEqual(reconstructed, img)

    def test_build_and_parse_hall_terminator(self):
        term = build_hall_terminator()
        self.assertEqual(len(term), 64)
        self.assertTrue(term.startswith(b"\xAA\x27\x10\xF0\x03\x00\x01"))
        self.assertEqual(term[7:64], b"\x00" * 57)

        size, flags = parse_hall_terminator(term)
        self.assertEqual(size, 1008)
        self.assertEqual(flags, b"\x00\x01")


class TestRGBGlobalProtocol(unittest.TestCase):
    """Test Global RGB (AA 23 10) encoder and decoder."""

    def test_build_and_parse_rgb_global(self):
        rep = build_rgb_global(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),
            secondary=(0, 255, 0),
            brightness=4,
            speed=2,
        )
        self.assertEqual(len(rep), 64)
        self.assertTrue(rep.startswith(b"\xAA\x23\x10\x00\x00\x00\x01\x00"))
        self.assertEqual(rep[22:24], b"\xAA\x55")
        self.assertEqual(rep[24:64], b"\x00" * 40)

        cfg = parse_rgb_global(rep)
        self.assertEqual(cfg.effect, EFFECT_STATIC)
        self.assertEqual(cfg.primary, (255, 0, 0))
        self.assertEqual(cfg.secondary, (0, 255, 0))
        self.assertEqual(cfg.brightness, 4)
        self.assertEqual(cfg.speed, 2)

        # Re-encode and verify exact byte match
        re_encoded = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            reserved_header=cfg.reserved_header,
            reserved_mid=cfg.reserved_mid,
            reserved_tail=cfg.reserved_tail,
        )
        self.assertEqual(re_encoded, rep)

    def test_build_rgb_global_validation(self):
        with self.assertRaises(ValueError):
            build_rgb_global(effect=256, primary=(0, 0, 0))  # invalid effect
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(256, 0, 0))  # invalid color
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), brightness=6)  # brightness > 5
        with self.assertRaises(ValueError):
            build_rgb_global(effect=1, primary=(0, 0, 0), speed=0)  # speed < 1


class TestRGBPerKeyProtocol(unittest.TestCase):
    """Test Per-Key RGB (AA 24) buffer construction and report chunking."""

    def test_build_and_parse_led_buffer(self):
        # Set slot 35 (Key W) to Green (0, 255, 0)
        colors = {35: (0, 255, 0), 0: (255, 0, 0)}
        buf = build_led_buffer(colors)
        self.assertEqual(len(buf), 512)

        # Check slot 0: [0, 255, 0, 0]
        self.assertEqual(buf[0:4], bytes([0, 255, 0, 0]))
        # Check slot 35: [35, 0, 255, 0]
        self.assertEqual(buf[35 * 4 : 36 * 4], bytes([35, 0, 255, 0]))
        # Check unconfigured slot 1: [1, 0, 0, 0]
        self.assertEqual(buf[4:8], bytes([1, 0, 0, 0]))

        # Parse back
        parsed_colors = parse_led_buffer(buf)
        self.assertEqual(parsed_colors[35], (0, 255, 0))
        self.assertEqual(parsed_colors[0], (255, 0, 0))
        self.assertEqual(parsed_colors[1], (0, 0, 0))

    def test_build_and_parse_per_key_chunks(self):
        buf = build_led_buffer({35: (0, 255, 0)})
        reports = build_rgb_per_key_chunks(buf)
        self.assertEqual(len(reports), 10)

        # Reports 0..8 must be 56-byte chunks (AA 24 38)
        for i in range(9):
            rep = reports[i]
            self.assertEqual(len(rep), 64)
            self.assertTrue(rep.startswith(b"\xAA\x24\x38"))
            self.assertEqual(rep[8:64], buf[i * 56 : (i + 1) * 56])

        # Report 9 must be 8-byte tail (AA 24 08 F8 01)
        rep_tail = reports[9]
        self.assertEqual(len(rep_tail), 64)
        self.assertTrue(rep_tail.startswith(b"\xAA\x24\x08\xF8\x01"))
        self.assertEqual(rep_tail[8:16], buf[504:512])
        self.assertEqual(rep_tail[16:64], b"\x00" * 48)

        # Parse back into 512 bytes
        reconstructed = parse_rgb_per_key_chunks(reports)
        self.assertEqual(reconstructed, buf)


class TestPhysicalLEDMapping(unittest.TestCase):
    """Verify that physical LED layout is strictly decoupled from Hall switch KEY_MAP."""

    def test_key_w_led_slot(self):
        # W must strictly be LED ID 35 (0x23)
        self.assertEqual(PHYSICAL_LED_MAP["W"], 35)
        self.assertEqual(PHYSICAL_LED_MAP["W"], 0x23)
        self.assertEqual(get_led_index("W"), 35)
        self.assertEqual(get_led_index("w"), 35)

        # Verify decoupling from Hall KEY_MAP
        hall_w_bank, hall_w_col = KEY_MAP["W"]
        self.assertEqual((hall_w_bank, hall_w_col), (2, 2))
        # Hall col is 2, whereas physical LED slot is 35!
        self.assertNotEqual(PHYSICAL_LED_MAP["W"], hall_w_col)
        self.assertNotEqual(PHYSICAL_LED_MAP["W"], hall_w_bank)

    def test_led_mapping_aliases(self):
        self.assertEqual(get_led_index("ESCAPE"), 0)
        self.assertEqual(get_led_index("ESC"), 0)
        self.assertEqual(get_led_index("PRINT"), 13)
        self.assertEqual(get_led_index("PRTSC"), 13)


class TestGoldenByteForByteRealCaptures(unittest.TestCase):
    """
    Golden round-trip tests on actual hardware captures from official WebHID configurator.
    Guarantees: encode(parse(real_capture)) == real_capture byte-for-byte.
    """

    @classmethod
    def setUpClass(cls):
        cls.exp_dir = Path("captures/experiments")
        cls.samples_dir = Path("captures/samples")

    def test_golden_hall_baseline_140mm(self):
        path = self.samples_dir / "baseline_140mm.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_reports = [bytes.fromhex(r.data_hex) for r in cap.reports]
        self.assertEqual(len(real_reports), 19)

        # 1. Parse real image from the 18 chunks
        image_bytes = parse_hall_write(real_reports[:18])
        self.assertEqual(len(image_bytes), 1008)

        # 2. Re-encode through build_hall_write
        re_encoded_chunks = build_hall_write(image_bytes)
        self.assertEqual(len(re_encoded_chunks), 18)

        # 3. Exact byte-for-byte verification for all 18 data chunks
        for i in range(18):
            self.assertEqual(
                re_encoded_chunks[i],
                real_reports[i],
                f"Hall chunk #{i} mismatch with baseline capture!"
            )

        # 4. Terminator verification
        term_size, term_flags = parse_hall_terminator(real_reports[18])
        self.assertEqual(term_size, 1008)
        self.assertEqual(term_flags, b"\x00\x01")
        re_encoded_term = build_hall_terminator(term_size, term_flags)
        self.assertEqual(
            re_encoded_term,
            real_reports[18],
            "Hall terminator mismatch with baseline capture!"
        )

    def test_golden_rgb_01_color(self):
        path = self.exp_dir / "rgb_01_color.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_report = bytes.fromhex(cap.reports[0].data_hex)

        # Parse -> Model -> Encode
        cfg = parse_rgb_global(real_report)
        self.assertEqual(cfg.effect, EFFECT_STATIC)
        self.assertEqual(cfg.primary, (255, 0, 0))
        self.assertEqual(cfg.driver_setting, 255)
        self.assertEqual(cfg.secondary, (0, 0, 0))
        self.assertEqual(cfg.color_mode, 0)
        self.assertEqual(cfg.brightness, 5)
        self.assertEqual(cfg.speed, 3)

        encoded = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            reserved_header=cfg.reserved_header,
            reserved_mid=cfg.reserved_mid,
            reserved_tail=cfg.reserved_tail,
        )
        self.assertEqual(encoded, real_report, "rgb_01_color byte-for-byte mismatch!")

    def test_golden_rgb_02_brightness(self):
        path = self.exp_dir / "rgb_02_brightness.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_report = bytes.fromhex(cap.reports[1].data_hex)

        cfg = parse_rgb_global(real_report)
        self.assertEqual(cfg.brightness, 3)

        encoded = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            reserved_header=cfg.reserved_header,
            reserved_mid=cfg.reserved_mid,
            reserved_tail=cfg.reserved_tail,
        )
        self.assertEqual(encoded, real_report, "rgb_02_brightness byte-for-byte mismatch!")

    def test_golden_rgb_03_effect(self):
        path = self.exp_dir / "rgb_03_effect.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_report = bytes.fromhex(cap.reports[0].data_hex)

        cfg = parse_rgb_global(real_report)
        self.assertEqual(cfg.effect, EFFECT_BREATHING)

        encoded = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            reserved_header=cfg.reserved_header,
            reserved_mid=cfg.reserved_mid,
            reserved_tail=cfg.reserved_tail,
        )
        self.assertEqual(encoded, real_report, "rgb_03_effect byte-for-byte mismatch!")

    def test_golden_rgb_04_speed(self):
        path = self.exp_dir / "rgb_04_speed.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_report = bytes.fromhex(cap.reports[0].data_hex)

        cfg = parse_rgb_global(real_report)
        self.assertEqual(cfg.speed, 5)

        encoded = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            reserved_header=cfg.reserved_header,
            reserved_mid=cfg.reserved_mid,
            reserved_tail=cfg.reserved_tail,
        )
        self.assertEqual(encoded, real_report, "rgb_04_speed byte-for-byte mismatch!")

    def test_golden_rgb_05_per_key(self):
        path = self.exp_dir / "rgb_05_per_key.json"
        if not path.exists():
            self.skipTest(f"{path} not found")

        cap = RawCapture.load_json(path)
        real_reports = [bytes.fromhex(r.data_hex) for r in cap.reports]

        # Report 0: Global custom mode activation
        cfg0 = parse_rgb_global(real_reports[0])
        self.assertEqual(cfg0.effect, EFFECT_CUSTOM)
        encoded_r0 = build_rgb_global(
            effect=cfg0.effect,
            primary=cfg0.primary,
            secondary=cfg0.secondary,
            brightness=cfg0.brightness,
            speed=cfg0.speed,
            reserved_header=cfg0.reserved_header,
            reserved_mid=cfg0.reserved_mid,
            reserved_tail=cfg0.reserved_tail,
        )
        self.assertEqual(encoded_r0, real_reports[0], "rgb_05_per_key report 0 mismatch!")

        # Reports 11..20 (Burst 1 where W is set to green)
        burst1_reps = real_reports[11:21]
        buffer_512 = parse_rgb_per_key_chunks(burst1_reps)
        self.assertEqual(len(buffer_512), 512)

        # Slot 34 in rgb_05_per_key capture has green LED (0, 255, 0)
        slot_34 = buffer_512[34 * 4 : 35 * 4]
        self.assertEqual(slot_34, bytes([34, 0, 255, 0]))

        # Re-encode and verify exact byte-for-byte match for all 10 reports
        re_encoded_chunks = build_rgb_per_key_chunks(buffer_512)
        self.assertEqual(len(re_encoded_chunks), 10)
        for i in range(10):
            self.assertEqual(
                re_encoded_chunks[i],
                burst1_reps[i],
                f"Per-Key report #{i} mismatch with rgb_05_per_key capture!"
            )


if __name__ == "__main__":
    unittest.main()
