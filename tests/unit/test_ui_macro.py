"""
Unit tests for MacrosView (Headless UI).

Verifies:
- Creation and initialization of MacrosView with AppController.
- Display of macro list and empty editor state when no macros exist.
- Adding a new macro via '+ New'.
- Adding actions (Tap / Press / Release) with delays.
- Removing an action event.
- Saving macro to working profile and verifying diff & dirty status.
- Deleting a macro.
"""

from __future__ import annotations

import unittest
import customtkinter as ctk

from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.macros_view import MacrosView


class TestMacrosView(unittest.TestCase):
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
        self.controller.connect(use_mock=True)
        self.view = MacrosView(self.root, controller=self.controller)

    def tearDown(self):
        self.view.destroy()
        self.controller.disconnect()

    def test_macros_view_initial_empty_state(self):
        self.assertEqual(len(self.controller.get_macro_list()), 0)
        self.view.update_display()
        self.assertIsNone(self.view.selected_macro_id)
        self.assertEqual(self.view.del_btn.cget("state"), "disabled")

    def test_macros_view_create_and_save_macro(self):
        # Click '+ New'
        self.view._on_new_macro()
        self.assertEqual(self.view.selected_macro_id, 0)
        self.assertEqual(self.view.del_btn.cget("state"), "normal")

        # Set macro name
        self.view.macro_name_entry.delete(0, "end")
        self.view.macro_name_entry.insert(0, "Quick Fire")

        # Configure action: Key 'W' (0x1A), Tap (Down+Up), delay 30ms
        self.view.key_menu.set("W")
        self.view.event_menu.set("Tap (Down+Up)")
        self.view.delay_entry.delete(0, "end")
        self.view.delay_entry.insert(0, "30")

        # Add action
        self.view._on_add_action()
        # Tap adds 2 events (Press + Release)
        self.assertEqual(len(self.view._current_actions), 2)
        self.assertEqual(self.view._current_actions[0].key_code, 0x1A)
        self.assertTrue(self.view._current_actions[0].is_press)
        self.assertEqual(self.view._current_actions[0].delay, 30)
        self.assertEqual(self.view._current_actions[1].key_code, 0x1A)
        self.assertFalse(self.view._current_actions[1].is_press)

        # Add single press action: Key 'Space', Press Only, delay 100ms
        self.view.key_menu.set("Space")
        self.view.event_menu.set("Press Only")
        self.view.delay_entry.delete(0, "end")
        self.view.delay_entry.insert(0, "100")
        self.view._on_add_action()
        self.assertEqual(len(self.view._current_actions), 3)

        # Remove the second event (Release W)
        self.view._remove_action(1)
        self.assertEqual(len(self.view._current_actions), 2)

        # Save to working profile
        self.view._on_save_macro()

        # Check controller
        self.assertTrue(self.controller.is_dirty)
        macro = self.controller.get_macro(0)
        self.assertIsNotNone(macro)
        self.assertEqual(macro.name, "Quick Fire")
        self.assertEqual(len(macro.actions), 2)

        # Delete macro
        self.view._on_delete_macro()
        self.assertIsNone(self.view.selected_macro_id)
        self.assertEqual(len(self.controller.get_macro_list()), 0)


if __name__ == "__main__":
    unittest.main()
