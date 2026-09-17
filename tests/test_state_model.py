"""
Unit tests for KeyboardState and KeyboardSnapshot models.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from keyboard_re.models.state import (
    HallProfileState,
    KeyboardProfile,
    KeyboardSnapshot,
    KeyboardState,
)


class TestStateModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_path = Path(__file__).resolve().parent.parent / "captures" / "experiments" / "read_01_initial_load.json"

    def test_load_snapshot_from_json(self):
        snapshot = KeyboardSnapshot.load_json(self.capture_path)
        self.assertIsInstance(snapshot, KeyboardSnapshot)
        self.assertEqual(snapshot.active_profile_id, 1)

        # Device info
        self.assertIsNotNone(snapshot.device_info)
        self.assertEqual(snapshot.device_info.vid, 0x0C45)
        self.assertEqual(snapshot.device_info.pid, 0x80D6)
        self.assertEqual(snapshot.device_info.firmware_version, "1.17")

        # Active profile
        prof1 = snapshot.active_profile
        self.assertEqual(prof1.profile_id, 1)

        # Key A actuation
        key_a = prof1.hall.get_key("A")
        self.assertEqual(key_a.actuation_mm, 1.40)
        self.assertEqual(key_a.actuation, 140)

        # Keymap layer 1
        slot_esc = prof1.keymap.get_slot(0, 0)
        self.assertEqual(slot_esc.hid_name, "Escape")

        # Fn layer
        self.assertIsNotNone(snapshot.fn_layer)
        slot_fn_bksp = snapshot.fn_layer.get_slot(5, 12)
        self.assertEqual(slot_fn_bksp.hid_name, "ScrollLock")

    def test_snapshot_immutability_and_cloning(self):
        snapshot = KeyboardSnapshot.load_json(self.capture_path)
        mutable_state = snapshot.clone_mutable()

        # Modify Key A in mutable copy
        mutable_state.active_profile.hall.set_actuation("A", 1.39)
        self.assertEqual(mutable_state.active_profile.hall.get_key("A").actuation_mm, 1.39)

        # Verify original snapshot was not modified
        self.assertEqual(snapshot.active_profile.hall.get_key("A").actuation_mm, 1.40)

        # Produce new snapshot
        snapshot2 = mutable_state.to_snapshot()
        self.assertEqual(snapshot2.active_profile.hall.get_key("A").actuation_mm, 1.39)


if __name__ == "__main__":
    unittest.main()
