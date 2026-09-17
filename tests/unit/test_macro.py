"""
Unit and Integration Tests for Macro Subsystem (AA 15 / AA 25).

Covers:
1. MacroAction serialization and flag encoding (bit 7 press, bits 4..6 action type).
2. MacroDefinition body serialization (4B header f = k * 2).
3. MacroCatalog 400B catalog + heap image generation and round-trip parsing.
4. Sequential AA 25 write chunk building (catalog and heap phases).
5. Profile integration: macro_catalog property, setter, and dump_raw_buffers.
6. ProfileDiff and ProfileWritePlan generation.
7. Mock execution via ProfilePlanExecutor.
8. AppController macro management (CRUD, dirty tracking, mock apply).
"""

from __future__ import annotations

import struct
import unittest

from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.executor import ProfilePlanExecutor, validate_chunk_ack
from keyboard_re.protocol.macro import (
    MACRO_BUFFER_SIZE,
    MACRO_CHUNK_SIZE,
    MACRO_HEAP_START,
    MACRO_READ_OPCODE,
    MACRO_SLOT_COUNT,
    MACRO_TAIL_ADDR,
    MACRO_TAIL_SIZE,
    MACRO_WRITE_OPCODE,
    MacroAction,
    MacroActionType,
    MacroCatalog,
    MacroDefinition,
    build_macro_write_chunks,
)
from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.transport import MockHidTransport
from keyboard_re.ui.controller import AppController


class TestMacroModels(unittest.TestCase):
    """Test MacroAction, MacroDefinition, and MacroCatalog models."""

    def test_macro_action_keyboard_press_and_release(self):
        # Key 'A' (0x04), Press, delay 100ms
        act_press = MacroAction(action_type=MacroActionType.KEYBOARD, is_press=True, key_code=0x04, delay=100)
        wire_press = act_press.to_bytes()
        self.assertEqual(len(wire_press), 4)

        # Byte 0..1: delay LE, Byte 2: keycode, Byte 3: flags (0x80 | (1 << 4) = 0x90)
        self.assertEqual(wire_press[0:2], struct.pack("<H", 100))
        self.assertEqual(wire_press[2], 0x04)
        self.assertEqual(wire_press[3], 0x90)

        # Release: bit 7 is 0 -> flags = 0x10
        act_rel = MacroAction(action_type=MacroActionType.KEYBOARD, is_press=False, key_code=0x04, delay=50)
        wire_rel = act_rel.to_bytes()
        self.assertEqual(wire_rel[3], 0x10)

        # Roundtrip
        dec = MacroAction.from_bytes(wire_press)
        self.assertEqual(dec.action_type, MacroActionType.KEYBOARD)
        self.assertTrue(dec.is_press)
        self.assertEqual(dec.key_code, 0x04)
        self.assertEqual(dec.delay, 100)

    def test_macro_action_mouse(self):
        # Mouse Button 1 (Left click), Press, flags = 0x80 | (3 << 4) = 0xB0
        act_mouse = MacroAction(action_type=MacroActionType.MOUSE, is_press=True, key_code=0x01, delay=20)
        wire = act_mouse.to_bytes()
        self.assertEqual(wire[3], 0xB0)

        dec = MacroAction.from_bytes(wire)
        self.assertEqual(dec.action_type, MacroActionType.MOUSE)
        self.assertTrue(dec.is_press)
        self.assertEqual(dec.key_code, 0x01)

    def test_macro_definition_body_serialization(self):
        actions = [
            MacroAction(action_type=1, is_press=True, key_code=0x1A, delay=0),    # W down
            MacroAction(action_type=1, is_press=False, key_code=0x1A, delay=50),  # W up
        ]
        macro = MacroDefinition(macro_id=0, name="Walk Forward", actions=actions)
        body = macro.to_body_bytes()

        # k = 2 actions, f = k * 2 = 4
        self.assertEqual(len(body), 4 + 2 * 4)  # 12 bytes
        self.assertEqual(body[0:4], bytes([0x04, 0x00, 0x00, 0x00]))

        # Roundtrip
        dec_macro = MacroDefinition.from_body_bytes(macro_id=0, data=body, name="Walk Forward")
        self.assertEqual(dec_macro.macro_id, 0)
        self.assertEqual(dec_macro.name, "Walk Forward")
        self.assertEqual(len(dec_macro.actions), 2)
        self.assertEqual(dec_macro.actions[0].key_code, 0x1A)
        self.assertTrue(dec_macro.actions[0].is_press)
        self.assertEqual(dec_macro.actions[1].key_code, 0x1A)
        self.assertFalse(dec_macro.actions[1].is_press)

    def test_macro_catalog_empty_and_populated(self):
        catalog = MacroCatalog()
        # Empty catalog builds 400 bytes of zeros for catalog and empty heap
        cat_b, heap_b = catalog.build_image()
        self.assertEqual(len(cat_b), MACRO_BUFFER_SIZE)
        self.assertEqual(cat_b, bytes(MACRO_BUFFER_SIZE))
        self.assertEqual(len(heap_b), 0)
        self.assertEqual(catalog.to_full_image(), bytes(MACRO_BUFFER_SIZE))

        # Add macro at slot 5
        actions = [
            MacroAction(action_type=1, is_press=True, key_code=0x04, delay=10),
            MacroAction(action_type=1, is_press=False, key_code=0x04, delay=10),
        ]
        macro5 = MacroDefinition(macro_id=5, name="Slot 5 Macro", actions=actions)
        catalog.set_macro(macro5)

        cat_b, heap_b = catalog.build_image()
        self.assertEqual(len(cat_b), MACRO_BUFFER_SIZE)
        # Pointer at slot 5 (offset 20) must be 400 (0x0190)
        ptr5 = struct.unpack_from("<I", cat_b, 5 * 4)[0]
        self.assertEqual(ptr5, MACRO_HEAP_START)

        # Other slots must remain 0
        self.assertEqual(struct.unpack_from("<I", cat_b, 0)[0], 0)
        self.assertEqual(struct.unpack_from("<I", cat_b, 4)[0], 0)

        # Heap length: 4B header + 2 * 4B = 12 bytes
        self.assertEqual(len(heap_b), 12)

        # Full image roundtrip
        full_img = catalog.to_full_image()
        self.assertEqual(len(full_img), 400 + 12)

        parsed_cat = MacroCatalog.from_full_image(full_img, names={5: "Slot 5 Macro"})
        self.assertIn(5, parsed_cat.macros)
        parsed_m5 = parsed_cat.get_macro(5)
        self.assertIsNotNone(parsed_m5)
        self.assertEqual(parsed_m5.name, "Slot 5 Macro")
        self.assertEqual(len(parsed_m5.actions), 2)
        self.assertEqual(parsed_m5.actions[0].key_code, 0x04)


class TestMacroWriteChunks(unittest.TestCase):
    """Test wire write chunk building (AA 25 / 55 25)."""

    def test_build_chunks_catalog_only(self):
        # 400 bytes catalog only (no heap)
        raw_catalog = bytes(MACRO_BUFFER_SIZE)
        chunks = build_macro_write_chunks(raw_catalog)

        # Must produce exactly 8 chunks (7 x 56B + 1 x 8B tail)
        self.assertEqual(len(chunks), 8)

        # First 7 chunks
        for i in range(7):
            c = chunks[i]
            self.assertEqual(c.chunk_index, i)
            self.assertEqual(c.address, i * MACRO_CHUNK_SIZE)
            self.assertEqual(c.size, MACRO_CHUNK_SIZE)
            self.assertEqual(c.packet[0:3], bytes([0xAA, MACRO_WRITE_OPCODE, MACRO_CHUNK_SIZE]))
            # is_last must be False (byte 6 == 0)
            self.assertEqual(c.packet[6], 0x00)
            self.assertEqual(c.expected_ack[0:3], bytes([0x55, MACRO_WRITE_OPCODE, MACRO_CHUNK_SIZE]))

        # Tail chunk
        tail = chunks[7]
        self.assertEqual(tail.chunk_index, 7)
        self.assertEqual(tail.address, MACRO_TAIL_ADDR)
        self.assertEqual(tail.size, MACRO_TAIL_SIZE)
        self.assertEqual(tail.packet[0:3], bytes([0xAA, MACRO_WRITE_OPCODE, MACRO_TAIL_SIZE]))
        # With no heap, catalog tail is the last packet of the transaction (byte 6 == 1)
        self.assertEqual(tail.packet[6], 0x01)
        self.assertEqual(tail.expected_ack[6], 0x01)

    def test_build_chunks_with_heap(self):
        # 400 bytes catalog + 80 bytes heap (requires 2 heap chunks: 56B + 24B)
        catalog = bytes(MACRO_BUFFER_SIZE)
        heap = bytes(range(80))
        full_image = catalog + heap

        chunks = build_macro_write_chunks(full_image)
        # 8 catalog chunks + 2 heap chunks = 10 chunks total
        self.assertEqual(len(chunks), 10)

        # Catalog tail (chunk 7) should NOT have is_last=True because heap follows
        self.assertEqual(chunks[7].packet[6], 0x00)

        # Heap chunk 0: addr 400, size 56, is_last=False
        heap_c0 = chunks[8]
        self.assertEqual(heap_c0.address, MACRO_HEAP_START)
        self.assertEqual(heap_c0.size, 56)
        self.assertEqual(heap_c0.packet[6], 0x00)

        # Heap chunk 1: addr 456, size 24, is_last=True
        heap_c1 = chunks[9]
        self.assertEqual(heap_c1.address, MACRO_HEAP_START + 56)
        self.assertEqual(heap_c1.size, 24)
        self.assertEqual(heap_c1.packet[6], 0x01)
        self.assertEqual(heap_c1.expected_ack[6], 0x01)


class TestMacroProfileIntegration(unittest.TestCase):
    """Test Profile, ProfileDiff, and ProfileWritePlan integration."""

    def setUp(self):
        transport = MockHidTransport()
        from keyboard_re.models.state import collect_device_state
        self.state = collect_device_state(transport)
        self.profile = self.state.create_profile(profile_id=1, name="Test Macro Profile")

    def test_profile_macro_catalog_property_and_setter(self):
        # Baseline has 400 bytes zeros -> empty catalog
        cat = self.profile.macro_catalog
        self.assertIsInstance(cat, MacroCatalog)
        self.assertEqual(len(cat.macros), 0)

        # Add a macro
        m = MacroDefinition(
            macro_id=1,
            name="Rapid Q",
            actions=[MacroAction(action_type=1, is_press=True, key_code=0x14, delay=10)],
        )
        cat.set_macro(m)
        self.profile.set_macro_catalog(cat)

        # Verify macros_raw is updated and has length > 400
        self.assertIsNotNone(self.profile.macros_raw)
        self.assertGreater(len(self.profile.macros_raw), 400)
        self.assertEqual(self.profile.metadata.get("macro_names", {}).get("1"), "Rapid Q")

        # Re-read through property
        readback_cat = self.profile.macro_catalog
        self.assertIn(1, readback_cat.macros)
        self.assertEqual(readback_cat.get_macro(1).name, "Rapid Q")

    def test_profile_diff_detects_macro_changes(self):
        mgr = ProfileManager()

        # Initial diff should be zero
        diff = mgr.compare_profile_with_state(self.profile, self.state)
        sub_macro = diff.get_subsystem("macro_raw")
        self.assertIsNotNone(sub_macro)
        self.assertFalse(sub_macro.has_changes)

        # Modify macro in profile
        cat = self.profile.macro_catalog
        cat.set_macro(MacroDefinition(
            macro_id=0,
            name="Test",
            actions=[MacroAction(action_type=1, is_press=True, key_code=0x04, delay=10)],
        ))
        self.profile.set_macro_catalog(cat)

        # Now diff should detect changes
        diff2 = mgr.compare_profile_with_state(self.profile, self.state)
        sub_macro2 = diff2.get_subsystem("macro_raw")
        self.assertTrue(sub_macro2.has_changes)
        self.assertIn("macro_raw", diff2.changed_subsystems)

    def test_write_plan_includes_macro_at_step_5(self):
        # Modify macro
        cat = self.profile.macro_catalog
        cat.set_macro(MacroDefinition(
            macro_id=2,
            name="Key Spam",
            actions=[
                MacroAction(action_type=1, is_press=True, key_code=0x04, delay=20),
                MacroAction(action_type=1, is_press=False, key_code=0x04, delay=20),
            ],
        ))
        self.profile.set_macro_catalog(cat)

        plan = build_profile_write_plan(self.state, self.profile)
        self.assertFalse(plan.is_empty)

        step = plan.get_step("macro")
        self.assertIsNotNone(step)
        self.assertEqual(step.opcode, 0x25)
        self.assertEqual(step.subsystem, "macro")
        # 8 catalog chunks + 1 heap chunk = 9 chunks
        self.assertEqual(step.packet_count, 9)

    def test_mock_execution_of_macro_plan(self):
        # Modify macro
        cat = self.profile.macro_catalog
        cat.set_macro(MacroDefinition(
            macro_id=0,
            name="Macro Zero",
            actions=[MacroAction(action_type=1, is_press=True, key_code=0x1A, delay=15)],
        ))
        self.profile.set_macro_catalog(cat)

        plan = build_profile_write_plan(self.state, self.profile)
        self.assertIn("macro", plan.modified_subsystems)

        # Create mock transport with valid ACK responses for AA 25
        transport = MockHidTransport()

        # Execute plan with readback disabled (since mock doesn't simulate flash heap)
        executor = ProfilePlanExecutor(transport=transport, strict_payload=False)
        result = executor.execute(plan, self.profile, verify_readback=False)

        self.assertTrue(result.is_success)
        self.assertEqual(result.executed_steps, 1)
        self.assertEqual(result.step_results[0].subsystem, "macro")


class TestMacroController(unittest.TestCase):
    """Test AppController macro methods, dirty tracking, and mock apply."""

    def setUp(self):
        self.controller = AppController()
        self.assertTrue(self.controller.connect(use_mock=True))

    def tearDown(self):
        self.controller.disconnect()

    def test_controller_macro_crud(self):
        # Initially empty list
        macros = self.controller.get_macro_list()
        self.assertEqual(len(macros), 0)
        self.assertFalse(self.controller.is_dirty)

        # Create macro
        new_macro = MacroDefinition(
            macro_id=0,
            name="Jump",
            actions=[
                MacroAction(action_type=1, is_press=True, key_code=0x2C, delay=0),
                MacroAction(action_type=1, is_press=False, key_code=0x2C, delay=50),
            ],
        )
        self.controller.save_macro(new_macro)

        # Must be dirty now
        self.assertTrue(self.controller.is_dirty)
        self.assertGreater(self.controller.diff_count, 0)

        # List must have 1 macro
        macros = self.controller.get_macro_list()
        self.assertEqual(len(macros), 1)
        self.assertEqual(macros[0].macro_id, 0)
        self.assertEqual(macros[0].name, "Jump")

        # Delete macro
        self.assertTrue(self.controller.delete_macro(0))
        self.assertEqual(len(self.controller.get_macro_list()), 0)

    def test_controller_mock_apply_clears_dirty(self):
        new_macro = MacroDefinition(
            macro_id=3,
            name="Crouch",
            actions=[MacroAction(action_type=1, is_press=True, key_code=0xE0, delay=10)],
        )
        self.controller.save_macro(new_macro)
        self.assertTrue(self.controller.is_dirty)

        # Apply changes in mock mode
        result = self.controller.apply_changes(confirmed=True)
        self.assertTrue(result.is_success)
        self.assertFalse(self.controller.is_dirty)
        self.assertEqual(self.controller.diff_count, 0)


if __name__ == "__main__":
    unittest.main()
