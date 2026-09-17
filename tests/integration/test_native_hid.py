"""
Hardware Integration Tests for NativeHidTransport.

Tests decorated with @requires_hardware are skipped during standard test discovery
unless KEYBOARD_HW_TESTS=1 environment variable is set.
"""

from __future__ import annotations

import os
import unittest

from keyboard_re.transport.native_hid import (
    NativeHidTransport,
    TARGET_PID,
    TARGET_PRODUCT_NAME,
    TARGET_USAGE,
    TARGET_USAGE_PAGE,
    TARGET_VID,
)


def requires_hardware(cls_or_func):
    """
    Decorator for integration tests requiring a physically attached IO Type 84 keyboard.
    Skipped by default in automated test runs unless KEYBOARD_HW_TESTS=1 is set.
    """
    is_hw_enabled = os.environ.get("KEYBOARD_HW_TESTS") == "1"
    return unittest.skipUnless(
        is_hw_enabled,
        "Requires physical hardware (enable with KEYBOARD_HW_TESTS=1)"
    )(cls_or_func)


class TestNativeHidTransportUnit(unittest.TestCase):
    """Unit tests for NativeHidTransport that run without hardware."""

    def test_init_defaults(self):
        t = NativeHidTransport()
        self.assertEqual(t.vid, TARGET_VID)
        self.assertEqual(t.pid, TARGET_PID)
        self.assertEqual(t.usage_page, TARGET_USAGE_PAGE)
        self.assertEqual(t.usage, TARGET_USAGE)
        self.assertEqual(t.product_name, TARGET_PRODUCT_NAME)
        self.assertFalse(t.is_connected)
        self.assertIsNone(t.device_path)

    def test_not_open_raises(self):
        t = NativeHidTransport()
        with self.assertRaises(ConnectionError):
            t.write_report(b"\x00" * 64)
        with self.assertRaises(ConnectionError):
            t.read_report()


@requires_hardware
class TestNativeHidTransportHardware(unittest.TestCase):
    """Live hardware integration tests (Read-Only)."""

    def setUp(self):
        self.transport = NativeHidTransport()

    def tearDown(self):
        if self.transport.is_connected:
            self.transport.close()

    def test_device_enumeration(self):
        path = self.transport.find_target_interface()
        self.assertIsNotNone(path)
        self.assertTrue(len(path) > 0)

    def test_open_close(self):
        self.transport.open()
        self.assertTrue(self.transport.is_connected)
        self.assertIn("Type 84", self.transport.actual_product_name or "")
        self.transport.close()
        self.assertFalse(self.transport.is_connected)

    def test_live_read_device_info(self):
        from keyboard_re.protocol.read import read_device_info, read_keyboard_status

        self.transport.open()
        info = read_device_info(self.transport)
        self.assertEqual(info.vid, TARGET_VID)
        self.assertEqual(info.pid, TARGET_PID)
        self.assertTrue(len(info.firmware_version) > 0)

        status = read_keyboard_status(self.transport)
        self.assertIn(status.active_profile, (1, 2))

    def test_live_read_hall_profile_1(self):
        from keyboard_re.protocol.read import read_hall_profile

        self.transport.open()
        img = read_hall_profile(self.transport, profile_id=1)
        self.assertEqual(len(img), 1008)


if __name__ == "__main__":
    unittest.main()
