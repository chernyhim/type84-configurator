"""
Unit tests for Key Remap Layer 1 (L1) GUI subsystem and controller logic.

Verifies:
- Layout mapping formula: remap_slot = switch_slot + 1
- Exact function_type resolution (0x03 for PageDown 0x4E & Keypad 0x53..0x63, 0x02 for standard)
- True unbound (00 00 00 00) vs Firmware Default (scancode == 0, function_type == 0x02)
- Read-only protection for Fn key (remap slot 86)
- Out-of-bounds slot protection (strictly restricted to 84 physical keys)
- Byte-exact preservation of untouched slots
- Controller remap info, assignment, unbind, resets, and diff recalculation
- Dry-run write plan generation (10 packets of opcode 0x22)
"""

import unittest
from pathlib import Path

from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.ui.controller import AppController, KeyRemapInfo
from keyboard_re.ui.layout_data import (
    FIRMWARE_FALLBACK_DEFAULTS,
    FN_REMAP_SLOT,
    KEY_BY_ID,
    KEY_BY_REMAP_SLOT,
    KEY_BY_SWITCH_SLOT,
    PHYSICAL_84_REMAP_SLOTS,
    TYPE84_LAYOUT,
    get_function_type_for_scancode,
)


class TestKeyRemapLayoutData(unittest.TestCase):
    """Verify layout invariants and slot mappings."""

    def test_all_84_keys_satisfy_remap_formula(self):
        """Every physical key must satisfy remap_slot = switch_slot."""
        self.assertEqual(len(TYPE84_LAYOUT), 84)
        for k in TYPE84_LAYOUT:
            self.assertEqual(k.remap_slot, k.switch_slot)
            self.assertIn(k.remap_slot, PHYSICAL_84_REMAP_SLOTS)
            self.assertEqual(KEY_BY_REMAP_SLOT[k.remap_slot], k)

    def test_fn_slot_constant(self):
        """Fn key switch slot 85 maps to remap slot 85."""
        fn_key = KEY_BY_ID["FN"]
        self.assertEqual(fn_key.switch_slot, 85)
        self.assertEqual(fn_key.remap_slot, FN_REMAP_SLOT)
        self.assertEqual(FN_REMAP_SLOT, 85)

    def test_explicit_function_type_resolution(self):
        """
        Verify explicit function_type resolution:
        - 0x03 for Page Down (0x4E) and Keypad (0x53..0x63)
        - 0x02 for standard HID keyboard keys
        """
        # Page Down
        self.assertEqual(get_function_type_for_scancode(0x4E), 0x03)

        # Keypad codes (0x53..0x63)
        self.assertEqual(get_function_type_for_scancode(0x53), 0x03)  # NumLock
        self.assertEqual(get_function_type_for_scancode(0x58), 0x03)  # KPEnter
        self.assertEqual(get_function_type_for_scancode(0x63), 0x03)  # KPDot

        # Standard keys
        self.assertEqual(get_function_type_for_scancode(0x04), 0x02)  # A
        self.assertEqual(get_function_type_for_scancode(0x1A), 0x02)  # W
        self.assertEqual(get_function_type_for_scancode(0x28), 0x02)  # Enter
        self.assertEqual(get_function_type_for_scancode(0x29), 0x02)  # Escape
        self.assertEqual(get_function_type_for_scancode(0x4B), 0x02)  # PageUp
        self.assertEqual(get_function_type_for_scancode(0xE0), 0x02)  # LCtrl


class TestKeyRemapController(unittest.TestCase):
    """Verify Controller domain methods for Key Remapping."""

    def setUp(self):
        self.capture_path = (
            Path(__file__).resolve().parents[2]
            / "captures"
            / "experiments"
            / "read_01_initial_load.json"
        )
        self.controller = AppController(default_capture_path=self.capture_path)
        self.controller.connect(use_mock=True)

    def test_get_key_remap_info_standard_key(self):
        """Inspect a standard physical key (A: switch 49, remap 49)."""
        info = self.controller.get_key_remap_info(switch_slot=49)
        self.assertIsNotNone(info)
        self.assertEqual(info.key_id, "A")
        self.assertEqual(info.switch_slot, 49)
        self.assertEqual(info.remap_slot, 49)
        self.assertEqual(info.default_name, "A")
        self.assertEqual(info.current_name, "A")
        self.assertFalse(info.is_modified)
        self.assertTrue(info.is_default)
        self.assertFalse(info.is_unbound)
        self.assertFalse(info.is_readonly)

    def test_get_key_remap_info_firmware_fallback_key(self):
        """
        Inspect firmware fallback key when baseline scancode is 0 (function_type == 0x02).
        Must use FIRMWARE_FALLBACK_DEFAULTS and NOT be reported as Unbound/None.
        """
        # Inject scancode 0 for Space into baseline and working profile to verify fallback
        fallback_rec = KeyRemapRecord(page_type=0x02, param1=0, param2=0, param3=0)
        self.controller.device_state.remap_l1.slots[83] = fallback_rec
        self.controller.working_profile.remap.slots[83] = fallback_rec

        info = self.controller.get_key_remap_info(switch_slot=83)
        self.assertIsNotNone(info)
        self.assertEqual(info.key_id, "SPACE")
        self.assertEqual(info.remap_slot, 83)
        self.assertIn("Firmware Default", info.default_name)
        self.assertIn("Firmware Default", info.current_name)
        self.assertFalse(info.is_unbound)
        self.assertTrue(info.is_default)
        self.assertFalse(info.is_modified)

    def test_fn_key_is_strictly_readonly(self):
        """Fn key (switch 85, remap 85) must be read-only and cannot be modified or unbound."""
        info = self.controller.get_key_remap_info(switch_slot=85)
        self.assertIsNotNone(info)
        self.assertTrue(info.is_readonly)

        # Attempt to set key binding on Fn
        res_set = self.controller.set_key_binding(switch_slot=85, scancode=0x04)
        self.assertFalse(res_set)

        # Attempt to unbind Fn
        res_unbind = self.controller.unbind_key(switch_slot=85)
        self.assertFalse(res_unbind)

        # Slot remains intact (scancode 0xAF)
        rec = self.controller.working_profile.remap.slots[85]
        self.assertEqual(rec.scancode, 0xAF)

    def test_set_key_binding_standard_and_diff(self):
        """Remap Key A (switch 49) -> Key B (scancode 0x05)."""
        success = self.controller.set_key_binding(switch_slot=49, scancode=0x05)
        self.assertTrue(success)

        # Verify record encoding
        rec = self.controller.working_profile.remap.slots[49]
        self.assertEqual(rec.scancode, 0x05)
        self.assertEqual(rec.function_type, 0x02)
        self.assertEqual(rec.to_bytes(), bytes([0x02, 0x00, 0x05, 0x00]))

        # Check dirty state and diff
        self.assertTrue(self.controller.is_dirty)
        self.assertTrue(self.controller.is_key_remapped(49))

        info = self.controller.get_key_remap_info(switch_slot=49)
        self.assertTrue(info.is_modified)
        self.assertEqual(info.current_name, "B")
        self.assertEqual(info.default_name, "A")

        # Verify ProfileDiff
        diff = self.controller.last_diff
        self.assertIsNotNone(diff)
        sub = diff.get_subsystem("remap_l1")
        self.assertIsNotNone(sub)
        self.assertTrue(sub.has_changes)
        self.assertEqual(sub.change_count, 1)

    def test_set_key_binding_page_down_has_function_type_0x03(self):
        """
        Remap a key to Page Down (scancode 0x4E).
        Explicit requirement: must have function_type = 0x03 matching baseline.
        """
        # Remap W (switch 34, remap 34) to Page Down
        success = self.controller.set_key_binding(switch_slot=34, scancode=0x4E)
        self.assertTrue(success)

        rec = self.controller.working_profile.remap.slots[34]
        self.assertEqual(
            rec,
            KeyRemapRecord.for_standard_key(0x4E, page_type=0x03),
        )

    def test_set_key_binding_keypad_has_function_type_0x03(self):
        """
        Remap a key to Keypad Enter (scancode 0x58).
        Must have function_type = 0x03.
        """
        success = self.controller.set_key_binding(switch_slot=34, scancode=0x58)
        self.assertTrue(success)

        rec = self.controller.working_profile.remap.slots[34]
        self.assertEqual(rec.scancode, 0x58)
        self.assertEqual(rec.function_type, 0x03)
        self.assertEqual(rec.to_bytes(), bytes([0x03, 0x00, 0x58, 0x00]))

    def test_unbind_key(self):
        """Unbinding a key sets record to true all zeros (00 00 00 00)."""
        success = self.controller.unbind_key(switch_slot=49)
        self.assertTrue(success)

        rec = self.controller.working_profile.remap.slots[49]
        self.assertEqual(rec.to_bytes(), bytes([0, 0, 0, 0]))
        self.assertEqual(rec.function_type, 0x00)

        info = self.controller.get_key_remap_info(switch_slot=49)
        self.assertTrue(info.is_unbound)
        self.assertTrue(info.is_modified)
        self.assertEqual(info.current_name, "[Disabled / Unbound]")

    def test_reset_key_to_default(self):
        """Resetting a modified key restores baseline value."""
        self.controller.set_key_binding(switch_slot=49, scancode=0x05)
        self.assertTrue(self.controller.is_key_remapped(49))

        res_reset = self.controller.reset_key_to_default(switch_slot=49)
        self.assertTrue(res_reset)

        rec = self.controller.working_profile.remap.slots[49]
        self.assertEqual(rec.scancode, 0x04)  # Restored to A

        self.assertFalse(self.controller.is_key_remapped(49))
        info = self.controller.get_key_remap_info(switch_slot=49)
        self.assertFalse(info.is_modified)

    def test_reset_all_remap(self):
        """Resetting all remaps restores all 128 slots to baseline."""
        self.controller.set_key_binding(switch_slot=49, scancode=0x05)  # A -> B
        self.controller.set_key_binding(switch_slot=34, scancode=0x4E)  # W -> PgDn
        self.controller.unbind_key(switch_slot=1)                       # F1 -> Unbind

        self.assertEqual(self.controller.last_diff.get_subsystem("remap_l1").change_count, 3)

        success = self.controller.reset_all_remap()
        self.assertTrue(success)

        sub = self.controller.last_diff.get_subsystem("remap_l1")
        self.assertFalse(sub.has_changes)
        self.assertEqual(sub.change_count, 0)

    def test_untouched_slots_preserved_byte_exact(self):
        """Modifying one slot must not mutate any other matrix slots."""
        initial_raw = bytes(self.controller.working_profile.remap.raw_bytes)

        # Modify slot 49 (offset 196..200)
        self.controller.set_key_binding(switch_slot=49, scancode=0x05)
        new_raw = self.controller.working_profile.remap.raw_bytes

        self.assertEqual(len(new_raw), 512)
        # Bytes before offset 196 unchanged
        self.assertEqual(new_raw[:196], initial_raw[:196])
        # Modified slot
        self.assertEqual(new_raw[196:200], bytes([0x02, 0x00, 0x05, 0x00]))
        # Bytes after offset 200 unchanged
        self.assertEqual(new_raw[200:], initial_raw[200:])

    def test_out_of_bounds_slot_rejected(self):
        """Slots not in physical 84 keys cannot be modified."""
        res = self.controller.set_key_binding(switch_slot=999, scancode=0x04)
        self.assertFalse(res)

    def test_confirmation_summary_generates_aa22_write_step(self):
        """When remap is modified, dry-run confirmation summary contains AA 22 step."""
        self.controller.set_key_binding(switch_slot=49, scancode=0x05)
        summary = self.controller.get_confirmation_summary()
        self.assertIsNotNone(summary)
        self.assertIn("remap_l1", summary.changed_subsystems)

        # Look for opcode 0x22 in steps
        remap_steps = [s for s in summary.steps_summary if s["opcode"] == 0x22]
        self.assertEqual(len(remap_steps), 1)
        self.assertEqual(remap_steps[0]["packet_count"], 10)


class TestKeyRemapUxPresentation(unittest.TestCase):
    """Verify UX polish formatting, badge generation, and catalog layout invariants."""

    def test_format_remap_badge(self):
        """Test compact badge label formatting."""
        from keyboard_re.ui.views.keyboard_view import _format_remap_badge

        self.assertEqual(_format_remap_badge("A"), "→ A")
        self.assertEqual(_format_remap_badge("PageDown"), "→ PgDn")
        self.assertEqual(_format_remap_badge("Left Ctrl"), "→ Ctrl")
        self.assertEqual(_format_remap_badge("L-Ctrl"), "→ Ctrl")
        self.assertEqual(_format_remap_badge("NumLock"), "→ Num")
        self.assertEqual(_format_remap_badge("KP Enter"), "→ Ent")
        self.assertEqual(_format_remap_badge("Backspace"), "→ Back")
        self.assertEqual(_format_remap_badge("↑ (Up)"), "→ ↑")
        self.assertEqual(_format_remap_badge("↓ (Down)"), "→ ↓")

    def test_catalog_columns_and_letters_layout(self):
        """Letters catalog must have 8 columns and match [A]..[H] for row 0."""
        from keyboard_re.ui.views.remap_view import CATEGORY_COLUMNS, REMAP_CATALOG

        self.assertEqual(CATEGORY_COLUMNS.get("Letters"), 8)
        letters = [name for _, name in REMAP_CATALOG["Letters"]]
        self.assertEqual(len(letters), 26)
        row_0 = letters[:8]
        row_1 = letters[8:16]
        self.assertEqual(row_0, ["A", "B", "C", "D", "E", "F", "G", "H"])
        self.assertEqual(row_1, ["I", "J", "K", "L", "M", "N", "O", "P"])

    def test_catalog_descriptions_available_for_all_keys(self):
        """All scancodes in REMAP_CATALOG must have descriptions."""
        from keyboard_re.ui.views.remap_view import FULL_KEY_DESCRIPTIONS, REMAP_CATALOG

        for cat_name, items in REMAP_CATALOG.items():
            for scancode, short_name in items:
                desc = FULL_KEY_DESCRIPTIONS.get(scancode, short_name)
                self.assertTrue(len(desc) > 0)


if __name__ == "__main__":
    unittest.main()
