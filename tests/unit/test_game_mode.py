"""
Unit tests for Game Mode / Performance Settings (AA 11 / AA 21).

Tests:
1. GameModeResponse serialization (to_payload), parsing, and round-trip.
2. Packet building (AA 21 38 00 00 00 01 00 ...) and ACK building (55 21 ...).
3. validate_game_mode_ack validation logic (prefix, opcode, size, flags, payload echo).
4. Profile and DeviceState serialization/deserialization with game_mode.
5. ProfileManager diffing logic (_diff_game_mode).
6. ProfileWritePlan integration (SubsystemWriteStep for game_mode, opcode 0x21).
7. ProfilePlanExecutor execution with mock transport.
8. AppController getters and setters for Game Mode settings.
"""

from pathlib import Path
import unittest

from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.game_mode import (
    GAME_MODE_PAYLOAD_SIZE,
    GAME_MODE_READ_OPCODE,
    GAME_MODE_WRITE_OPCODE,
    build_game_mode_expected_ack,
    build_game_mode_write_chunk,
    build_game_mode_write_packet,
    validate_game_mode_ack,
)
from keyboard_re.protocol.executor import ProfilePlanExecutor
from keyboard_re.protocol.plan import SubsystemWriteStep, build_profile_write_plan
from keyboard_re.protocol.read import GameModeResponse, parse_game_mode_response
from keyboard_re.ui.controller import AppController


class TestGameModeProtocol(unittest.TestCase):
    """Test low-level packet building, serialization, and ACK validation."""

    def test_default_game_mode_response(self):
        gm = GameModeResponse()
        self.assertEqual(gm.sleep_time, 1)
        self.assertEqual(gm.report_rate, 6)
        self.assertEqual(gm.report_rate_hz, 8000)
        self.assertEqual(gm.stability_mode, 1)
        self.assertEqual(gm.auto_calibration, 1)
        self.assertEqual(gm.game_mode, 0)
        self.assertEqual(gm.fn_switch, 0)
        self.assertEqual(gm.key_delay, 0)
        self.assertEqual(gm.system_mode, 0)
        self.assertEqual(gm.top_deadzone, 0.0)
        self.assertEqual(gm.bottom_deadzone, 0.0)

    def test_report_rate_hz_conversions(self):
        gm = GameModeResponse()
        gm.set_report_rate_hz(1000)
        self.assertEqual(gm.report_rate, 3)
        self.assertEqual(gm.report_rate_hz, 1000)

        gm.set_report_rate_hz(4000)
        self.assertEqual(gm.report_rate, 5)
        self.assertEqual(gm.report_rate_hz, 4000)

        gm.set_report_rate_hz(8000)
        self.assertEqual(gm.report_rate, 6)
        self.assertEqual(gm.report_rate_hz, 8000)

    def test_to_payload_serialization(self):
        gm = GameModeResponse(
            game_mode=1,
            fn_switch=1,
            sleep_time=5,
            key_delay=2,
            report_rate=5,  # 4000 Hz
            system_mode=1,
            tft_display_time=3,
            top_deadzone=0.15,
            bottom_deadzone=0.25,
            stability_mode=0,
            auto_calibration=1,
            single_key_wakeup=1,
            push_button_mode=0,
        )
        payload = gm.to_payload()
        self.assertEqual(len(payload), 56)
        self.assertEqual(payload[1], 1)   # gameMode
        self.assertEqual(payload[2], 1)   # fnSwitch
        self.assertEqual(payload[3], 5)   # sleepTime
        self.assertEqual(payload[4], 2)   # keyDelay
        self.assertEqual(payload[5], 5)   # reportRate
        self.assertEqual(payload[6], 1)   # systemMode
        self.assertEqual(payload[7], 3)   # tftDisplayTime
        self.assertEqual(payload[8], 15)  # topDeadZone * 100
        self.assertEqual(payload[9], 25)  # bottomDeadZone * 100
        self.assertEqual(payload[11], 0)  # stabilityMode
        self.assertEqual(payload[14], 1)  # autoCalibration
        self.assertEqual(payload[15], 1)  # singleKeyWakeup
        self.assertEqual(payload[16], 0)  # pushButtonMode

    def test_build_write_packet_and_ack(self):
        gm = GameModeResponse()
        pkt = build_game_mode_write_packet(gm)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[:8], bytes([0xAA, 0x21, 0x38, 0x00, 0x00, 0x00, 0x01, 0x00]))
        self.assertEqual(pkt[8:64], gm.to_payload())

        ack = build_game_mode_expected_ack(gm)
        self.assertEqual(len(ack), 64)
        self.assertEqual(ack[:8], bytes([0x55, 0x21, 0x38, 0x00, 0x00, 0x00, 0x01, 0x00]))
        self.assertEqual(ack[8:64], gm.to_payload())

    def test_validate_game_mode_ack(self):
        gm = GameModeResponse()
        valid_ack = build_game_mode_expected_ack(gm)
        self.assertIsNone(validate_game_mode_ack(valid_ack, expected_payload=gm.to_payload()))

        # Wrong size
        self.assertIn("Invalid report size", validate_game_mode_ack(valid_ack[:60]))

        # Wrong prefix
        bad_prefix = bytearray(valid_ack)
        bad_prefix[0] = 0xAA
        self.assertIn("Invalid ACK prefix", validate_game_mode_ack(bad_prefix))

        # Wrong opcode
        bad_op = bytearray(valid_ack)
        bad_op[1] = 0x22
        self.assertIn("Invalid ACK opcode", validate_game_mode_ack(bad_op))

        # Payload mismatch
        mismatch_payload = bytearray(gm.to_payload())
        mismatch_payload[11] ^= 0x01
        self.assertIn("payload echo mismatch", validate_game_mode_ack(valid_ack, expected_payload=bytes(mismatch_payload)))

    def test_parse_game_mode_response_roundtrip(self):
        gm = GameModeResponse(
            game_mode=1,
            fn_switch=0,
            sleep_time=10,
            key_delay=4,
            report_rate=3,
            system_mode=0,
            top_deadzone=0.10,
            bottom_deadzone=0.20,
            stability_mode=1,
            auto_calibration=0,
        )
        report = bytearray(64)
        report[:8] = bytes([0x55, 0x11, 0x38, 0x00, 0x00, 0x00, 0x01, 0x00])
        report[8:64] = gm.to_payload()

        parsed = parse_game_mode_response(bytes(report))
        self.assertEqual(parsed.game_mode, 1)
        self.assertEqual(parsed.fn_switch, 0)
        self.assertEqual(parsed.sleep_time, 10)
        self.assertEqual(parsed.key_delay, 4)
        self.assertEqual(parsed.report_rate, 3)
        self.assertEqual(parsed.report_rate_hz, 1000)
        self.assertEqual(parsed.system_mode, 0)
        self.assertAlmostEqual(parsed.top_deadzone, 0.10, places=2)
        self.assertAlmostEqual(parsed.bottom_deadzone, 0.20, places=2)
        self.assertEqual(parsed.stability_mode, 1)
        self.assertEqual(parsed.auto_calibration, 0)


class TestGameModeStateAndDiff(unittest.TestCase):
    """Test DeviceState, Profile, diffing, and write plan integration."""

    @classmethod
    def setUpClass(cls):
        capture_path = (
            Path(__file__).parent.parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.state = DeviceState.load_json(capture_path)
        cls.manager = ProfileManager()

    def test_initial_state_has_game_mode(self):
        self.assertIsNotNone(self.state.game_mode)
        self.assertEqual(self.state.game_mode.sleep_time, 1)
        self.assertEqual(self.state.game_mode.report_rate, 6)
        self.assertEqual(self.state.game_mode.stability_mode, 1)
        self.assertEqual(self.state.game_mode.auto_calibration, 1)

    def test_profile_creation_inherits_game_mode(self):
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="GM Profile")
        self.assertIsNotNone(profile.game_mode)
        self.assertEqual(profile.game_mode.report_rate, self.state.game_mode.report_rate)

        # Ensure deep copy
        profile.game_mode.stability_mode = 0
        self.assertEqual(self.state.game_mode.stability_mode, 1)

    def test_diff_game_mode_identical(self):
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Identical GM")
        diff = self.manager.compare_profile_with_state(profile, self.state)
        sub = diff.get_subsystem("game_mode")
        self.assertIsNotNone(sub)
        self.assertFalse(sub.has_changes)
        self.assertIn("device-global", sub.summary)

    def test_diff_game_mode_modified(self):
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Mod GM")
        profile.game_mode.stability_mode = 0
        profile.game_mode.set_report_rate_hz(1000)

        diff = self.manager.compare_profile_with_state(profile, self.state)
        self.assertTrue(diff.has_changes)
        self.assertIn("game_mode", diff.changed_subsystems)

        sub = diff.get_subsystem("game_mode")
        self.assertTrue(sub.has_changes)
        self.assertEqual(sub.change_count, 2)
        param_names = [p.parameter for p in sub.details]
        self.assertIn("Stability Mode", param_names)
        self.assertIn("Report Rate", param_names)

    def test_write_plan_includes_game_mode(self):
        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Plan GM")
        profile.game_mode.stability_mode = 0

        plan = build_profile_write_plan(self.state, profile)
        self.assertFalse(plan.is_empty)
        self.assertIn("game_mode", plan.modified_subsystems)
        self.assertIn(0x21, plan.opcodes)

        step = plan.get_step("game_mode")
        self.assertIsNotNone(step)
        self.assertEqual(step.opcode, 0x21)
        self.assertEqual(step.packet_count, 1)
        self.assertEqual(step.chunks[0].size, 56)
        self.assertEqual(step.chunks[0].address, 0)
        self.assertEqual(step.chunks[0].packet[:2], bytes([0xAA, 0x21]))
        self.assertEqual(step.chunks[0].expected_ack[:2], bytes([0x55, 0x21]))

    def test_executor_with_mock_transport(self):
        class MockGameModeTransport:
            def __init__(self):
                self.sent_packets = []

            def send_report(self, report_id, report):
                self.sent_packets.append(bytes(report))

            def receive_report(self, timeout=1.0):
                last_pkt = self.sent_packets[-1]
                # Echo as ACK (55 21 ...)
                ack = bytearray(last_pkt)
                ack[0] = 0x55
                return bytes(ack)

        profile = self.manager.create_profile_from_state(self.state, profile_id=1, name="Exec GM")
        profile.game_mode.stability_mode = 0

        plan = build_profile_write_plan(self.state, profile)
        transport = MockGameModeTransport()

        def mock_readback(tr, timeout):
            cloned = self.state.clone()
            cloned.game_mode = profile.game_mode.clone()
            return cloned

        executor = ProfilePlanExecutor(transport=transport, readback_func=mock_readback)
        result = executor.execute(plan, target_profile=profile)
        self.assertTrue(result.is_success)
        self.assertEqual(len(transport.sent_packets), 1)
        self.assertEqual(transport.sent_packets[0][:2], bytes([0xAA, 0x21]))


class TestGameModeAppController(unittest.TestCase):
    """Test AppController integration with Game Mode settings."""

    @classmethod
    def setUpClass(cls):
        capture_path = (
            Path(__file__).parent.parent.parent
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        cls.state = DeviceState.load_json(capture_path)

    def setUp(self):
        self.controller = AppController()
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self):
        self.controller.disconnect()

    def test_get_game_mode_info(self):
        info = self.controller.get_game_mode_info()
        self.assertIsNotNone(info)
        self.assertEqual(info.report_rate_hz, 8000)
        self.assertEqual(info.stability_mode, 1)

    def test_set_stability_mode_triggers_diff(self):
        self.assertFalse(self.controller.is_dirty)
        self.controller.set_stability_mode(False)
        self.assertTrue(self.controller.is_dirty)
        self.assertEqual(self.controller.get_game_mode_info().stability_mode, 0)

    def test_set_report_rate_hz(self):
        self.controller.set_report_rate_hz(1000)
        self.assertEqual(self.controller.get_game_mode_info().report_rate, 3)
        self.assertEqual(self.controller.get_game_mode_info().report_rate_hz, 1000)

    def test_reset_game_mode_to_default(self):
        self.controller.set_stability_mode(False)
        self.controller.set_report_rate_hz(1000)
        self.assertTrue(self.controller.is_dirty)

        self.controller.reset_game_mode_to_default()
        self.assertFalse(self.controller.is_dirty)
        info = self.controller.get_game_mode_info()
        self.assertEqual(info.stability_mode, 1)
        self.assertEqual(info.report_rate_hz, 8000)


if __name__ == "__main__":
    unittest.main()
