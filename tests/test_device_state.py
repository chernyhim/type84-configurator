"""
Comprehensive unit tests for unified DeviceState and Profile architecture.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from keyboard_re.models.base import KEY_MAP
from keyboard_re.models.state import (
    DeviceState,
    HallProfileState,
    Profile,
    collect_device_state,
)
from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.read import DeviceInfoResponse, KeyboardStatusResponse
from keyboard_re.protocol.rgb import RGBGlobalConfig
from keyboard_re.protocol.transport import MockHidTransport


class TestDeviceStateArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_path = (
            Path(__file__).resolve().parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )

    def test_load_from_capture_events(self):
        """Test parsing read_01_initial_load.json into complete DeviceState."""
        state = DeviceState.load_json(self.capture_path)

        # 1. Device Info (AA 10)
        self.assertIsNotNone(state.device_info)
        self.assertEqual(state.device_info.vid, 0x0C45)
        self.assertEqual(state.device_info.pid, 0x80D6)
        self.assertEqual(state.device_info.firmware_version, "1.17")
        self.assertEqual(state.device_info.bootloader_version, "1.66")
        self.assertEqual(len(state.device_info.chip_id), 4)

        # 2. Performance Settings / GameMode (AA 11)
        self.assertIsNotNone(state.game_mode)
        self.assertEqual(state.game_mode.sleep_time, 1)
        self.assertEqual(state.game_mode.report_rate, 6)
        self.assertEqual(state.game_mode.stability_mode, 1)
        self.assertEqual(state.game_mode.auto_calibration, 1)
        self.assertEqual(len(state.game_mode.raw_payload), 16)
        self.assertEqual(state.active_profile_id, 1)

        # 3. Remap L1 (AA 12)
        self.assertIsNotNone(state.remap_l1)
        self.assertEqual(len(state.remap_l1.raw_bytes), 512)
        slot_esc = state.remap_l1.get_slot(0, 0)
        self.assertEqual(slot_esc.hid_name, "Escape")
        slot_a = state.remap_l1.get_slot(3, 1)
        self.assertEqual(slot_a.hid_name, "A")

        # 4. Remap L2 / Fn (AA 16)
        self.assertIsNotNone(state.remap_l2)
        self.assertEqual(len(state.remap_l2.raw_bytes), 512)
        slot_fn_bksp = state.remap_l2.get_slot(5, 12)
        self.assertEqual(slot_fn_bksp.hid_name, "ScrollLock")

        # 5. Remap L3 / Alt raw table (AA 1C)
        self.assertIsNotNone(state.remap_l3_raw)
        self.assertEqual(len(state.remap_l3_raw), 512)

        # 6. Global RGB (AA 13)
        self.assertIsNotNone(state.rgb_global)
        self.assertEqual(state.rgb_global.effect, 15)
        self.assertEqual(state.rgb_global.brightness, 5)
        self.assertEqual(state.rgb_global.speed, 5)

        # 7. Per-Key RGB matrix (AA 14)
        self.assertIsNotNone(state.rgb_matrix_raw)
        self.assertEqual(len(state.rgb_matrix_raw), 512)

        # 8. Macro table (AA 15)
        self.assertIsNotNone(state.macro_table_raw)
        self.assertEqual(len(state.macro_table_raw), 400)

        # 9. Hall Profile 1 (AA 17)
        self.assertIsNotNone(state.hall_profile_1)
        self.assertEqual(len(state.hall_profile_1.raw_image), 1008)
        key_a_p1 = state.hall_profile_1.get_key("A")
        self.assertAlmostEqual(key_a_p1.actuation_mm, 1.40, places=2)
        self.assertAlmostEqual(key_a_p1.rt_press_mm, 0.0, places=2)
        self.assertAlmostEqual(key_a_p1.rt_release_mm, 0.0, places=2)
        self.assertEqual(key_a_p1.flags, 0x0000)

        # 10. DKS Table (AA 18, 1024B, no hall_profile_2)
        self.assertIsNotNone(state.dks_table_raw)
        self.assertEqual(len(state.dks_table_raw), 1024)
        self.assertIsNone(state.hall_profile_2)

    def test_profile_abstraction(self):
        """Test Profile object methods, properties, and aliases."""
        state = DeviceState.load_json(self.capture_path)

        # Active profile retrieval
        active = state.active_profile
        self.assertIsInstance(active, Profile)
        self.assertEqual(active.profile_id, 1)

        # Hall access via Profile
        self.assertAlmostEqual(active.get_key("A").actuation_mm, 1.40, places=2)
        self.assertAlmostEqual(active.hall.get_key("A").actuation_mm, 1.40, places=2)

        # Remap / Keymap access via Profile
        self.assertEqual(active.remap.get_slot(0, 0).hid_name, "Escape")
        self.assertEqual(active.keymap.get_slot(0, 0).hid_name, "Escape")

        # RGB & Macros & DKS via Profile
        self.assertEqual(active.rgb_global.effect, 15)
        self.assertIsNotNone(active.rgb_matrix)
        self.assertEqual(len(active.rgb_matrix), 512)
        self.assertEqual(active.rgb_per_key, active.rgb_matrix)
        self.assertIsNotNone(active.macros_raw)
        self.assertEqual(len(active.macros_raw), 400)
        self.assertIsNotNone(active.macros)
        self.assertIsNotNone(active.dks_raw)
        self.assertEqual(len(active.dks_raw), 1024)

        # Software-defined profile cloning / creation
        p2 = active.clone()
        p2.profile_id = 2
        state.profiles[2] = p2
        self.assertEqual(state.get_profile(2).profile_id, 2)

        # Cloning isolation
        p1_clone = active.clone()
        p1_clone.hall.set_actuation("A", 0.50)
        self.assertAlmostEqual(p1_clone.get_key("A").actuation_mm, 0.50, places=2)
        self.assertAlmostEqual(active.get_key("A").actuation_mm, 1.40, places=2)

    def test_raw_buffers_round_trip(self):
        """Test 100% byte-exact raw buffer dump and reconstruction."""
        state = DeviceState.load_json(self.capture_path)
        buffers = state.dump_raw_buffers()

        # Check all buffers are present
        expected_keys = {
            "remap_l1",
            "remap_l2",
            "remap_l3",
            "rgb_global",
            "rgb_matrix",
            "macro_table",
            "hall_p1",
            "dks_table",
        }
        for k in expected_keys:
            self.assertIn(k, buffers, f"Buffer {k} missing from dump")

        self.assertEqual(len(buffers["remap_l1"]), 512)
        self.assertEqual(len(buffers["remap_l2"]), 512)
        self.assertEqual(len(buffers["remap_l3"]), 512)
        self.assertEqual(len(buffers["rgb_global"]), 64)
        self.assertEqual(len(buffers["rgb_matrix"]), 512)
        self.assertEqual(len(buffers["macro_table"]), 400)
        self.assertEqual(len(buffers["hall_p1"]), 1008)
        self.assertEqual(len(buffers["dks_table"]), 1024)

        # Reconstruct from raw buffers
        reconstructed = DeviceState.from_raw_buffers(buffers)
        reconstructed_bufs = reconstructed.dump_raw_buffers()

        # Verify 100% exact byte match for all buffers
        for k in expected_keys:
            self.assertEqual(
                buffers[k],
                reconstructed_bufs[k],
                f"Buffer {k} mismatch after round-trip!",
            )

    def test_dict_and_json_serialization_round_trip(self):
        """Test to_dict / from_dict and to_json / from_json serialization round-trip."""
        state = DeviceState.load_json(self.capture_path)
        data_dict = state.to_dict()

        # Reconstruct from dict
        from_dict_state = DeviceState.from_dict(data_dict)
        self.assertEqual(from_dict_state.active_profile_id, state.active_profile_id)
        self.assertEqual(from_dict_state.device_info.vid, state.device_info.vid)
        self.assertEqual(from_dict_state.device_info.pid, state.device_info.pid)
        self.assertEqual(from_dict_state.device_info.firmware_version, state.device_info.firmware_version)

        # Compare raw buffers
        orig_bufs = state.dump_raw_buffers()
        dict_bufs = from_dict_state.dump_raw_buffers()
        for k, buf in orig_bufs.items():
            if k in ("device_info", "status", "game_mode"):
                continue  # Handshake header is synthesized in dict
            self.assertEqual(buf, dict_bufs[k], f"Buffer {k} mismatch from dict round-trip")

        # JSON file save & load round-trip
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = Path(tf.name)

        try:
            state.save_json(temp_path)
            loaded_state = DeviceState.load_json(temp_path)
            loaded_bufs = loaded_state.dump_raw_buffers()
            for k, buf in orig_bufs.items():
                if k in ("device_info", "status", "game_mode"):
                    continue
                self.assertEqual(buf, loaded_bufs[k], f"Buffer {k} mismatch from JSON round-trip")
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_collect_device_state_with_mock_transport(self):
        """Test that collect_device_state executes all 10 read queries without any writes."""
        ref_state = DeviceState.load_json(self.capture_path)

        transport = MockHidTransport(auto_ack=False)
        with open(self.capture_path, "r", encoding="utf-8") as f:
            capture_data = json.load(f)

        for event in capture_data["events"]:
            if event.get("direction") == "DEVICE -> HOST":
                transport.queue_response(bytes.fromhex(event["data_hex"]))

        collected = collect_device_state(transport, timeout=1.0)

        self.assertIsInstance(collected, DeviceState)
        self.assertEqual(collected.active_profile_id, 1)
        self.assertEqual(collected.device_info.vid, 0x0C45)
        self.assertEqual(collected.device_info.pid, 0x80D6)
        self.assertEqual(collected.game_mode.sleep_time, 1)
        self.assertEqual(collected.game_mode.report_rate, 6)

        self.assertEqual(collected.remap_l1.raw_bytes, ref_state.remap_l1.raw_bytes)
        self.assertEqual(collected.remap_l2.raw_bytes, ref_state.remap_l2.raw_bytes)
        self.assertEqual(collected.remap_l3_raw, ref_state.remap_l3_raw)
        self.assertEqual(collected.rgb_global.effect, ref_state.rgb_global.effect)
        self.assertEqual(collected.rgb_matrix_raw, ref_state.rgb_matrix_raw)
        self.assertEqual(collected.macro_table_raw, ref_state.macro_table_raw)
        self.assertEqual(collected.hall_profile_1.to_bytes(), ref_state.hall_profile_1.to_bytes())
        self.assertEqual(collected.dks_table_raw, ref_state.dks_table_raw)
        self.assertEqual(len(collected.dks_table_raw), 1024)
        self.assertIsNone(collected.hall_profile_2)

        # Verify that all outgoing packets sent were strictly read opcodes (AA 1X)
        read_opcodes = {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x1C}
        for _, report in transport.recorded_reports:
            self.assertEqual(report[0], 0xAA, "All outgoing packets must start with 0xAA")
            self.assertIn(report[1], read_opcodes, f"Unexpected write opcode 0x{report[1]:02X} detected!")


if __name__ == "__main__":
    unittest.main()
