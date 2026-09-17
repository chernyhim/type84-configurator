"""
Unit tests for Profile Management, Discard Changes, Tab Renaming, and CLI integration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import customtkinter as ctk

from keyboard_re.cli import cmd_gui, main as cli_main
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.ui.app import KeyboardApp
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.layout_data import KEY_BY_ID
from keyboard_re.ui.views.main_window import MainWindow
from keyboard_re.ui.views.status_bar import StatusBarView


class TestProfileManagementAndUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.controller = AppController()
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self):
        self.controller.disconnect()

    def test_discard_all_changes(self):
        """discard_all_changes resets working_profile to device_state and clears dirty flag."""
        # Baseline check
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)

        # Mutate RGB brightness
        orig_bright = self.controller.working_profile.rgb_global.brightness
        new_bright = 5 if orig_bright != 5 else 3
        self.controller.set_rgb_brightness(new_bright)
        self.assertTrue(self.controller.is_dirty)
        self.assertGreater(self.controller.diff_count, 0)

        # Discard changes
        notified = []
        self.controller.subscribe(lambda: notified.append(True))
        self.controller.discard_all_changes()

        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)
        self.assertEqual(self.controller.working_profile.rgb_global.brightness, orig_bright)
        self.assertTrue(len(notified) > 0)

    def test_save_and_load_profile_disk(self):
        """Save working profile to disk and load it back, updating diff and notifications."""
        # Mutate RGB speed
        orig_speed = self.controller.working_profile.rgb_global.speed
        new_speed = 5 if orig_speed != 5 else 1
        self.controller.set_rgb_speed(new_speed)
        self.assertTrue(self.controller.is_dirty)

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_profile.json"

            # Save to disk
            saved_path = self.controller.save_profile_to_disk(file_path)
            self.assertTrue(saved_path.is_file())

            # Discard so working profile is back to baseline
            self.controller.discard_all_changes()
            self.assertFalse(self.controller.is_dirty)
            self.assertEqual(self.controller.working_profile.rgb_global.speed, orig_speed)

            # Load from disk
            notified = []
            self.controller.subscribe(lambda: notified.append(True))
            loaded_profile = self.controller.load_profile_from_disk(saved_path)

            self.assertIsInstance(loaded_profile, Profile)
            self.assertEqual(self.controller.working_profile.rgb_global.speed, new_speed)
            self.assertTrue(self.controller.is_dirty)
            self.assertGreater(self.controller.diff_count, 0)
            self.assertTrue(len(notified) > 0)

    def test_save_profile_without_working_profile_raises(self):
        """Attempting to save when no working profile is active raises RuntimeError."""
        self.controller.working_profile = None
        with self.assertRaises(RuntimeError):
            self.controller.save_profile_to_disk("some_path.json")

    def test_load_profile_missing_file_raises(self):
        """Attempting to load a non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.controller.load_profile_from_disk("non_existent_file_xyz.json")

    def test_status_bar_discard_button_state(self):
        """Discard button in StatusBarView is enabled only when controller is dirty."""
        bar = StatusBarView(self.root, controller=self.controller)
        try:
            # Initially clean -> disabled
            bar.update_display()
            self.assertEqual(bar.discard_btn.cget("state"), "disabled")

            # Dirty -> enabled
            orig_speed = self.controller.working_profile.rgb_global.speed
            self.controller.set_rgb_speed(5 if orig_speed != 5 else 1)
            bar.update_display()
            self.assertEqual(bar.discard_btn.cget("state"), "normal")

            # Click discard
            bar._handle_discard()
            bar.update_display()
            self.assertFalse(self.controller.is_dirty)
            self.assertEqual(bar.discard_btn.cget("state"), "disabled")
        finally:
            bar.destroy()

    def test_main_window_header_profile_buttons(self):
        """MainWindow includes Save Profile... and Load Profile... buttons in header."""
        window = MainWindow(self.root, controller=self.controller)
        try:
            self.assertTrue(hasattr(window, "save_profile_btn"))
            self.assertTrue(hasattr(window, "load_profile_btn"))
            self.assertEqual(window.save_profile_btn.cget("text"), "Save Profile...")
            self.assertEqual(window.load_profile_btn.cget("text"), "Load Profile...")

            # When connected -> normal
            window._on_controller_update()
            self.assertEqual(window.save_profile_btn.cget("state"), "normal")
            self.assertEqual(window.load_profile_btn.cget("state"), "normal")

            # When disconnected -> disabled
            self.controller.disconnect()
            window._on_controller_update()
            self.assertEqual(window.save_profile_btn.cget("state"), "disabled")
            self.assertEqual(window.load_profile_btn.cget("state"), "disabled")
        finally:
            window.destroy()

    def test_dks_tab_name_and_no_placeholder(self):
        """Tab 5 is named 'DKS' and MainWindow._build_placeholder is removed."""
        window = MainWindow(self.root, controller=self.controller)
        try:
            # Check tab exists with name 'DKS'
            self.assertTrue(hasattr(window, "dks_view"))
            # Dead code check: _build_placeholder must not exist
            self.assertFalse(hasattr(window, "_build_placeholder"))

            # Switch to DKS tab
            window.tabview.set("DKS")
            window._on_tab_changed()
            self.assertEqual(self.controller.active_tab, "dks")
        finally:
            window.destroy()

    def test_cli_gui_parser(self):
        """Test CLI parses 'gui' subcommand with optional '--mock'."""
        with patch("keyboard_re.cli.cmd_gui") as mock_cmd_gui:
            mock_cmd_gui.return_value = 0
            # Test 'kb-re gui'
            cli_main(["gui"])
            mock_cmd_gui.assert_called_once()
            args = mock_cmd_gui.call_args[0][0]
            self.assertFalse(args.mock)

        with patch("keyboard_re.cli.cmd_gui") as mock_cmd_gui:
            mock_cmd_gui.return_value = 0
            # Test 'kb-re gui --mock'
            cli_main(["gui", "--mock"])
            mock_cmd_gui.assert_called_once()
            args = mock_cmd_gui.call_args[0][0]
            self.assertTrue(args.mock)

    def test_cli_cmd_gui_invokes_app(self):
        """cmd_gui instantiates KeyboardApp and calls run()."""
        args = argparse.Namespace(mock=True)
        with patch("keyboard_re.ui.app.KeyboardApp") as MockApp:
            instance = MagicMock()
            MockApp.return_value = instance
            res = cmd_gui(args)
            self.assertEqual(res, 0)
            MockApp.assert_called_once_with(auto_connect_mock=True)
            instance.run.assert_called_once()

    def test_type84_gui_main_entrypoint(self):
        """type84-gui main parses CLI arguments and runs KeyboardApp."""
        from keyboard_re.ui.app import main as app_main
        with patch("keyboard_re.ui.app.KeyboardApp") as MockApp:
            instance = MagicMock()
            MockApp.return_value = instance
            app_main(["--mock"])
            MockApp.assert_called_once_with(auto_connect_mock=True)
            instance.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
