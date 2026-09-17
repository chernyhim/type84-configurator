"""
Unit and golden tests for read protocol parsers and request encoders.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.read import (
    DKS_BUFFER_SIZE,
    DeviceInfoResponse,
    GameModeResponse,
    KeyboardStatusResponse,
    build_read_request,
    parse_dks_read_chunks,
    parse_game_mode_response,
    parse_hall_read_chunks,
    parse_handshake_response,
    parse_rgb_global_read_response,
    parse_rgb_per_key_read_chunks,
    parse_status_response,
)


class TestReadProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"
        with open(capture_path, "r", encoding="utf-8") as f:
            cls.capture_data = json.load(f)

    def test_build_read_request_basic(self):
        req = build_read_request(opcode_low_nibble=0x7, address=0x0070, chunk_size=56)
        self.assertEqual(len(req), REPORT_SIZE)
        self.assertEqual(req[0], 0xAA)
        self.assertEqual(req[1], 0x17)
        self.assertEqual(req[2], 0x38)
        self.assertEqual(req[3], 0x70)
        self.assertEqual(req[4], 0x00)
        self.assertEqual(req[10:], b"\x00" * 54)

    def test_build_read_request_handshake(self):
        req = build_read_request(opcode_low_nibble=0x0, address=0, chunk_size=0x30)
        self.assertEqual(len(req), REPORT_SIZE)
        self.assertEqual(req[:3], b"\xAA\x10\x30")

    def test_parse_handshake_response_golden(self):
        # Event index 2 in read_01_initial_load.json
        resp_hex = self.capture_data["events"][2]["data_hex"]
        resp_bytes = bytes.fromhex(resp_hex)
        info = parse_handshake_response(resp_bytes)

        self.assertIsInstance(info, DeviceInfoResponse)
        self.assertEqual(info.vid, 0x0C45)
        self.assertEqual(info.pid, 0x80D6)
        self.assertEqual(info.firmware_version, "1.17")
        self.assertEqual(info.bootloader_version, "1.66")
        self.assertEqual(info.raw_header, bytes.fromhex("5510300000000100"))

    def test_parse_handshake_response_invalid_prefix(self):
        bad_rep = bytearray(64)
        bad_rep[0:2] = b"\x55\x20"
        with self.assertRaises(ValueError):
            parse_handshake_response(bad_rep)

    def test_parse_status_response_golden(self):
        # Event index 4 in read_01_initial_load.json
        resp_hex = self.capture_data["events"][4]["data_hex"]
        resp_bytes = bytes.fromhex(resp_hex)
        status = parse_status_response(resp_bytes)

        self.assertIsInstance(status, KeyboardStatusResponse)
        self.assertEqual(status.active_profile, 1)
        self.assertEqual(status.lock_flags, 0x06)
        self.assertEqual(status.mode_flag, 0x01)
        self.assertEqual(status.connection_flag, 0x01)
        self.assertEqual(len(status.payload_16b), 16)

    def test_parse_status_response_invalid(self):
        bad_rep = bytearray(64)
        bad_rep[0:2] = b"\xAA\x11"
        with self.assertRaises(ValueError):
            parse_status_response(bad_rep)

    def test_parse_rgb_global_read_response_golden(self):
        # Event index 66 in read_01_initial_load.json
        resp_hex = self.capture_data["events"][66]["data_hex"]
        resp_bytes = bytes.fromhex(resp_hex)
        cfg = parse_rgb_global_read_response(resp_bytes)

        self.assertEqual(cfg.effect, 15)  # Custom RGB
        self.assertEqual(cfg.primary, (255, 255, 255))
        self.assertEqual(cfg.secondary, (0, 0, 0))
        self.assertEqual(cfg.brightness, 5)
        self.assertEqual(cfg.speed, 5)

    def test_parse_rgb_global_read_response_invalid(self):
        bad_rep = bytearray(64)
        with self.assertRaises(ValueError):
            parse_rgb_global_read_response(bad_rep)

    def test_parse_rgb_per_key_read_chunks_golden(self):
        rgb_reports = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551438", "551408"))
        ]
        self.assertEqual(len(rgb_reports), 10)
        led_buf = parse_rgb_per_key_read_chunks(rgb_reports)
        self.assertEqual(len(led_buf), 512)

        # Slot 126 should be [126, 0, 0, 0] in golden capture
        slot_offset = 126 * 4
        self.assertEqual(led_buf[slot_offset : slot_offset + 4], bytes([126, 0, 0, 0]))

    def test_parse_rgb_per_key_read_chunks_validation(self):
        with self.assertRaises(ValueError):
            parse_rgb_per_key_read_chunks([bytes(64)] * 9)

        reports = [bytearray(64) for _ in range(10)]
        with self.assertRaises(ValueError):
            parse_rgb_per_key_read_chunks(reports)

    def test_parse_hall_read_chunks_golden(self):
        hall_reports = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551738", "551710"))
        ]
        self.assertEqual(len(hall_reports), 19)
        hall_img = parse_hall_read_chunks(hall_reports)
        self.assertEqual(len(hall_img), 1008)

        # 84 active keys in configuration image
        active_slots = [i for i in range(0, 1008, 8) if hall_img[i:i+8] != b"\x00" * 8]
        self.assertEqual(len(active_slots), 84)

    def test_parse_game_mode_response_golden(self):
        resp_hex = self.capture_data["events"][4]["data_hex"]
        resp_bytes = bytes.fromhex(resp_hex)
        gm = parse_game_mode_response(resp_bytes)

        self.assertIsInstance(gm, GameModeResponse)
        self.assertEqual(gm.sleep_time, 1)
        self.assertEqual(gm.report_rate, 6)
        self.assertEqual(gm.stability_mode, 1)
        self.assertEqual(gm.auto_calibration, 1)
        self.assertEqual(len(gm.raw_payload), 16)

    def test_parse_dks_read_chunks_golden(self):
        dks_reports = [
            bytes.fromhex(e["data_hex"])
            for e in self.capture_data["events"]
            if e["direction"] == "DEVICE -> HOST" and e["data_hex"].startswith(("551838", "551810"))
        ]
        self.assertEqual(len(dks_reports), 19)
        dks_buf = parse_dks_read_chunks(dks_reports)
        self.assertEqual(len(dks_buf), 1024)
        self.assertTrue(all(b == 0 for b in dks_buf[:1008]))

    def test_parse_dks_read_chunks_validation(self):
        with self.assertRaises(ValueError):
            parse_dks_read_chunks([bytes(64)] * 17)


if __name__ == "__main__":
    unittest.main()
