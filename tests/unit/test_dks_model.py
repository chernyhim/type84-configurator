"""
Unit tests for DKS (Dynamic Keystroke) domain models and binary encoders/decoders.

Focus areas:
- 16-byte DKSRecord roundtrip serialization and bitmask parsing.
- 1024-byte DKSTable buffer reconstruction (64 slots x 16 bytes).
- Strict evidence tier separation:
  1. TestDKSPhysicalCaptures (Factory baseline, Exp A, Exp B, Exp C).
  2. TestDKSStructuralInference (Actions 3..4, Points 1..3 induction).
  3. TestDKSCollisionPrecedence (Documented Python HOLD-before-TAP decoder behavior).
"""

from __future__ import annotations

import pytest

from keyboard_re.models.dks import (
    DEFAULT_BREAK_VALUE_1_MM,
    DEFAULT_BREAK_VALUE_2_MM,
    DEFAULT_MAKE_VALUE_1_MM,
    DEFAULT_MAKE_VALUE_2_MM,
    DKS_BUFFER_SIZE,
    DKS_RECORD_SIZE,
    DKS_SLOT_COUNT,
    DKSEventState,
    DKSRecord,
    DKSTable,
)


class TestDKSRecord:
    """Tests for 16-byte DKSRecord serialization, parsing, and bitmask logic."""

    def test_empty_record(self):
        rec = DKSRecord(
            make_value_1_mm=0.0,
            make_value_2_mm=0.0,
            break_value_1_mm=0.0,
            break_value_2_mm=0.0,
            actions=[0, 0, 0, 0],
            states=[[DKSEventState.OFF] * 4 for _ in range(4)],
        )
        assert rec.is_empty
        raw = rec.to_bytes()
        assert len(raw) == DKS_RECORD_SIZE
        assert raw == bytes(16)

        # Roundtrip from zeros
        decoded = DKSRecord.from_bytes(raw)
        assert decoded.is_empty
        assert decoded.to_bytes() == raw

    def test_record_encoding_fields_and_bitmasks(self):
        # Action 1: Tap at Point 0, Hold at Point 1
        # Action 2: Hold at Point 1, Tap at Point 2
        # Action 3: Off
        # Action 4: Tap at Point 3
        actions = [0x1A, 0xE1, 0x00, 0x2C]  # W, LShift, None, Space
        states = [
            [DKSEventState.TAP, DKSEventState.HOLD, DKSEventState.OFF, DKSEventState.OFF],  # Action 1
            [DKSEventState.OFF, DKSEventState.HOLD, DKSEventState.TAP, DKSEventState.OFF],  # Action 2
            [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF],   # Action 3
            [DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.TAP],   # Action 4
        ]
        rec = DKSRecord(
            make_value_1_mm=1.5,
            make_value_2_mm=2.8,
            break_value_1_mm=2.8,
            break_value_2_mm=1.5,
            actions=actions,
            states=states,
        )
        raw = rec.to_bytes()
        assert len(raw) == 16

        # Check travel thresholds (0.1 mm scaled by 10)
        assert raw[0] == 15   # make1 = 1.5 mm
        assert raw[1] == 28   # make2 = 2.8 mm
        assert raw[2] == 28   # break1 = 2.8 mm
        assert raw[3] == 15   # break2 = 1.5 mm

        # Check actions & reserved bytes
        assert raw[4] == 0x00
        assert raw[5] == 0x1A  # W
        assert raw[6] == 0x00
        assert raw[7] == 0xE1  # LShift
        assert raw[8] == 0x00
        assert raw[9] == 0x00  # Unbound
        assert raw[10] == 0x00
        assert raw[11] == 0x2C # Space

        # Point 0: Action 1 TAP -> low nibble bit 0 set (0x01), high nibble 0
        assert raw[12] == 0x01
        # Point 1: Action 1 HOLD (high bit 0 -> 0x10) | Action 2 HOLD (high bit 1 -> 0x20) -> 0x30
        assert raw[13] == 0x30
        # Point 2: Action 2 TAP -> low nibble bit 1 set (0x02), high nibble 0
        assert raw[14] == 0x02
        # Point 3: Action 4 TAP -> low nibble bit 3 set (0x08), high nibble 0
        assert raw[15] == 0x08

        # Exact roundtrip
        decoded = DKSRecord.from_bytes(raw)
        assert pytest.approx(decoded.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(decoded.make_value_2_mm, abs=0.01) == 2.8
        assert pytest.approx(decoded.break_value_1_mm, abs=0.01) == 2.8
        assert pytest.approx(decoded.break_value_2_mm, abs=0.01) == 1.5
        assert decoded.actions == actions
        assert decoded.states == states
        assert decoded.to_bytes() == raw

    def test_invalid_length_raises(self):
        with pytest.raises(ValueError, match="requires 16 bytes"):
            DKSRecord.from_bytes(bytes(15))


class TestDKSTable:
    """Tests for 1024-byte DKSTable (64 slots x 16 bytes)."""

    def test_empty_table_to_bytes(self):
        table = DKSTable()
        raw = table.to_bytes()
        assert len(raw) == DKS_BUFFER_SIZE
        assert raw == bytes(1024)

    def test_set_and_get_record(self):
        table = DKSTable()
        rec = DKSRecord(
            make_value_1_mm=DEFAULT_MAKE_VALUE_1_MM,
            make_value_2_mm=DEFAULT_MAKE_VALUE_2_MM,
            break_value_1_mm=DEFAULT_BREAK_VALUE_1_MM,
            break_value_2_mm=DEFAULT_BREAK_VALUE_2_MM,
            actions=[0x04, 0, 0, 0],
            states=[[DKSEventState.TAP, DKSEventState.OFF, DKSEventState.OFF, DKSEventState.OFF]] + [[DKSEventState.OFF]*4]*3,
        )
        table.set_record(5, rec)
        assert table.get_record(5).actions[0] == 0x04

        raw = table.to_bytes()
        assert len(raw) == 1024
        # Slot 5 is at offset 5 * 16 = 80
        assert raw[80 + 5] == 0x04

        # Roundtrip
        decoded = DKSTable.from_bytes(raw)
        assert decoded.get_record(5).actions[0] == 0x04
        assert decoded.get_record(0).is_empty
        assert decoded.to_bytes() == raw

    def test_allocate_and_free_slot(self):
        table = DKSTable()
        # First available slot should be 0
        s0 = table.allocate_slot()
        assert s0 == 0

        # Fill slot 0
        table.set_record(0, DKSRecord(actions=[0x04, 0, 0, 0]))
        s1 = table.allocate_slot()
        assert s1 == 1

        # Clear slot 0
        table.clear_record(0)
        s0_again = table.allocate_slot()
        assert s0_again == 0

    def test_out_of_bounds_slot_raises(self):
        table = DKSTable()
        with pytest.raises(IndexError):
            table.set_record(64, DKSRecord())
        with pytest.raises(IndexError):
            table.set_record(-1, DKSRecord())

    def test_clone_isolation(self):
        table = DKSTable()
        table.set_record(0, DKSRecord(actions=[0x1A, 0, 0, 0]))
        cloned = table.clone()
        cloned.set_record(0, DKSRecord(actions=[0x04, 0, 0, 0]))

        assert table.get_record(0).actions[0] == 0x1A
        assert cloned.get_record(0).actions[0] == 0x04


class TestDKSProtocolAlignment:
    """Regression tests verifying protocol wire alignment with 8-byte headers."""

    def test_parse_dks_read_chunks_golden_8byte_headers(self):
        """Verify 19 read chunks parse with 8-byte headers and exact payload slicing."""
        import struct
        from keyboard_re.protocol.read import parse_dks_read_chunks

        # Create a synthetic 1024-byte buffer with Slot 0 matching physical vendor capture:
        # 0F 1E 1E 0F 00 1A 00 E1 00 E1 00 1A 01 12 12 01
        synthetic_1024 = bytearray(1024)
        vendor_slot0 = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 E1 00 1A 01 12 12 01")
        synthetic_1024[0:16] = vendor_slot0
        for i in range(16, 1024):
            synthetic_1024[i] = (i * 13) & 0xFF

        reports = []
        for i in range(18):
            addr = i * 56
            rep = bytearray(64)
            rep[0] = 0x55
            rep[1] = 0x18
            rep[2] = 0x38
            struct.pack_into("<H", rep, 3, addr)
            rep[5:8] = bytes(3)  # subcmd, isLast, reserved
            rep[8 : 8 + 56] = synthetic_1024[addr : addr + 56]
            reports.append(bytes(rep))

        # Tail chunk
        rep_tail = bytearray(64)
        rep_tail[0] = 0x55
        rep_tail[1] = 0x18
        rep_tail[2] = 0x10
        struct.pack_into("<H", rep_tail, 3, 1008)
        rep_tail[5] = 0x00
        rep_tail[6] = 0x01  # isLastPacket
        rep_tail[7] = 0x00
        rep_tail[8 : 8 + 16] = synthetic_1024[1008 : 1008 + 16]
        reports.append(bytes(rep_tail))

        parsed_bytes = parse_dks_read_chunks(reports)
        assert len(parsed_bytes) == 1024
        assert parsed_bytes == bytes(synthetic_1024)

        # Decode table and inspect Slot 0
        table = DKSTable.from_bytes(parsed_bytes)
        slot0 = table.get_record(0)
        assert pytest.approx(slot0.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(slot0.make_value_2_mm, abs=0.01) == 3.0
        assert pytest.approx(slot0.break_value_1_mm, abs=0.01) == 3.0
        assert pytest.approx(slot0.break_value_2_mm, abs=0.01) == 1.5
        assert slot0.actions[0] == 0x1A  # W
        assert slot0.actions[1] == 0xE1  # LShift

    def test_read_dks_table_wire_structure_and_is_last_flag(self):
        """Verify read_dks_table issues requests and parses reports at index 8."""
        import struct
        from keyboard_re.protocol.read import read_dks_table
        from keyboard_re.protocol.transport import MockHidTransport

        transport = MockHidTransport(auto_ack=False)
        for i in range(18):
            addr = i * 56
            rep = bytearray(64)
            rep[0] = 0x55
            rep[1] = 0x18
            rep[2] = 0x38
            struct.pack_into("<H", rep, 3, addr)
            rep[8 : 8 + 56] = bytes([i] * 56)
            transport.queue_response(bytes(rep))

        rep_tail = bytearray(64)
        rep_tail[0] = 0x55
        rep_tail[1] = 0x18
        rep_tail[2] = 0x10
        struct.pack_into("<H", rep_tail, 3, 1008)
        rep_tail[6] = 0x01
        rep_tail[8 : 8 + 16] = bytes([18] * 16)
        transport.queue_response(bytes(rep_tail))

        result = read_dks_table(transport)
        assert len(result) == 1024

        # Verify outgoing requests
        sent_requests = [r for _, r in transport.recorded_reports]
        assert len(sent_requests) == 19
        tail_req = sent_requests[18]
        assert tail_req[0:3] == b"\xAA\x18\x10"
        assert struct.unpack_from("<H", tail_req, 3)[0] == 1008
        assert tail_req[6] == 0x01  # isLastPacket flag

    def test_remap_1_to_1_wire_alignment_records(self):
        """Verify 1:1 physical key to remap slot wire records."""
        from keyboard_re.protocol.keymap import KeyRemapRecord

        # Esc -> 02 00 29 00
        rec_esc = KeyRemapRecord.for_standard_key(0x29)
        assert rec_esc.to_bytes() == bytes([0x02, 0x00, 0x29, 0x00])

        # Q -> 02 00 14 00
        rec_q = KeyRemapRecord.for_standard_key(0x14)
        assert rec_q.to_bytes() == bytes([0x02, 0x00, 0x14, 0x00])

        # W (DKS Slot 0) -> 08 00 00 00
        rec_w_dks = KeyRemapRecord.for_dks(0)
        assert rec_w_dks.to_bytes() == bytes([0x08, 0x00, 0x00, 0x00])
        assert rec_w_dks.is_dks
        assert rec_w_dks.dks_slot_index == 0

        # E -> 02 00 08 00
        rec_e = KeyRemapRecord.for_standard_key(0x08)
        assert rec_e.to_bytes() == bytes([0x02, 0x00, 0x08, 0x00])

        # A -> 02 00 04 00
        rec_a = KeyRemapRecord.for_standard_key(0x04)
        assert rec_a.to_bytes() == bytes([0x02, 0x00, 0x04, 0x00])

        # Fn -> 02 00 AF 00
        rec_fn = KeyRemapRecord.for_standard_key(0xAF)
        assert rec_fn.to_bytes() == bytes([0x02, 0x00, 0xAF, 0x00])

    def test_write_plan_is_last_flag_and_8byte_header(self):
        """Verify AA 28 and AA 22 write chunks have 8-byte headers and is_last on tail."""
        from keyboard_re.protocol.plan import (
            build_dks_write_chunks,
            build_remap_write_chunks,
        )

        # DKS (AA 28)
        dks_buf = bytes(1024)
        dks_chunks = build_dks_write_chunks(dks_buf)
        assert len(dks_chunks) == 19
        for i in range(18):
            c = dks_chunks[i]
            assert c.packet[0:3] == b"\xAA\x28\x38"
            assert c.packet[6] == 0x00  # is_last False
            assert c.expected_ack[0:3] == b"\x55\x28\x38"
            assert c.expected_ack[6] == 0x00
        # Tail chunk
        tail_c = dks_chunks[18]
        assert tail_c.packet[0:3] == b"\xAA\x28\x10"
        assert tail_c.packet[6] == 0x01  # is_last True
        assert tail_c.expected_ack[0:3] == b"\x55\x28\x10"
        assert tail_c.expected_ack[6] == 0x01

        # Remap (AA 22)
        remap_buf = bytes(512)
        remap_chunks = build_remap_write_chunks(remap_buf)
        assert len(remap_chunks) == 10
        for i in range(9):
            c = remap_chunks[i]
            assert c.packet[0:3] == b"\xAA\x22\x38"
            assert c.packet[6] == 0x00
            assert c.expected_ack[0:3] == b"\x55\x22\x38"
            assert c.expected_ack[6] == 0x00
        tail_remap = remap_chunks[9]
        assert tail_remap.packet[0:3] == b"\xAA\x22\x08"
        assert tail_remap.packet[6] == 0x01
        assert tail_remap.expected_ack[0:3] == b"\x55\x22\x08"
        assert tail_remap.expected_ack[6] == 0x01


class TestDKSPhysicalCaptures:
    """
    Tier 1: Tests containing ONLY physically observed hardware captures from Type 84:
    - Factory / vendor baseline: Slot 0 = 0F 1E 1E 0F 00 1A 00 E1 00 E1 00 1A 00 00 00 00
    - Experiment A: Action 1 TAP -> byte +12 = 0x01
    - Experiment B: Action 1 HOLD -> byte +12 = 0x10
    - Experiment C: Action 2 TAP -> byte +7 = 0xE1, byte +12 = 0x02
    - Final Control Test: Action 2 HOLD -> byte +12 = 0x20
    - Microtests A1/A2: Action 3 TAP (0x04) / HOLD (0x40) at Point 0
    - Microtests B1/B2: Action 4 TAP (0x08) / HOLD (0x80) at Point 0
    - Microtests C1/C2: Point 1 (+13) TAP (0x01) / HOLD (0x10)
    - Microtests D1/D2: Point 2 (+14) TAP (0x01) / HOLD (0x10)
    - Microtests E1/E2: Point 3 (+15) TAP (0x01) / HOLD (0x10)
    """

    def test_physical_baseline_decoding_and_roundtrip(self):
        # Factory Slot 0 raw record from physical baseline capture:
        # Travel: make1=1.5mm (0x0F), make2=3.0mm (0x1E), break1=3.0mm (0x1E), break2=1.5mm (0x0F)
        # Actions: W (0x1A), LShift (0xE1), LShift (0xE1), W (0x1A)
        # State bytes (+12..+15): 00 00 00 00 (all OFF)
        raw_baseline_slot0 = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 E1 00 1A 00 00 00 00")
        rec = DKSRecord.from_bytes(raw_baseline_slot0)

        assert pytest.approx(rec.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(rec.make_value_2_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_1_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_2_mm, abs=0.01) == 1.5
        assert rec.actions == [0x1A, 0xE1, 0xE1, 0x1A]
        for a_idx in range(4):
            for p_idx in range(4):
                assert rec.states[a_idx][p_idx] == DKSEventState.OFF

        # Exact byte-for-byte serialization
        assert rec.to_bytes() == raw_baseline_slot0

    def test_physical_exp_a_action1_tap(self):
        # Experiment A: Point 0 Action 1 = TAP -> state byte +12 becomes 0x01
        # Slot 0 raw record from scratch/dks_capture_exp_a_tap.bin
        raw_exp_a = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 00 00 00 01 00 00 00")
        rec = DKSRecord.from_bytes(raw_exp_a)

        assert pytest.approx(rec.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(rec.make_value_2_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_1_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_2_mm, abs=0.01) == 1.5
        assert rec.actions == [0x1A, 0x00, 0x00, 0x00]
        # Point 0 Action 1 is TAP
        assert rec.states[0][0] == DKSEventState.TAP
        # All other 15 states are OFF
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF

        # Exact roundtrip
        assert rec.to_bytes() == raw_exp_a

    def test_physical_exp_b_action1_hold(self):
        # Experiment B: Point 0 Action 1 = HOLD -> state byte +12 becomes 0x10
        # Slot 0 raw record from scratch/dks_capture_exp_b_hold.bin
        raw_exp_b = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 00 00 00 10 00 00 00")
        rec = DKSRecord.from_bytes(raw_exp_b)

        assert pytest.approx(rec.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(rec.make_value_2_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_1_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_2_mm, abs=0.01) == 1.5
        assert rec.actions == [0x1A, 0x00, 0x00, 0x00]
        # Point 0 Action 1 is HOLD
        assert rec.states[0][0] == DKSEventState.HOLD
        # All other 15 states are OFF
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF

        # Exact roundtrip
        assert rec.to_bytes() == raw_exp_b

    def test_physical_exp_c_action2_tap(self):
        # Experiment C: Action 2 = LShift (0xE1), Point 0 Action 2 = TAP -> byte +7 = 0xE1, byte +12 = 0x02
        # Slot 0 raw record from scratch/dks_capture_exp_c_act2_tap.bin
        raw_exp_c = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 02 00 00 00")
        rec = DKSRecord.from_bytes(raw_exp_c)

        assert pytest.approx(rec.make_value_1_mm, abs=0.01) == 1.5
        assert pytest.approx(rec.make_value_2_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_1_mm, abs=0.01) == 3.0
        assert pytest.approx(rec.break_value_2_mm, abs=0.01) == 1.5
        assert rec.actions == [0x1A, 0xE1, 0x00, 0x00]
        # Point 0 Action 1 is OFF, Action 2 is TAP
        assert rec.states[0][0] == DKSEventState.OFF
        assert rec.states[1][0] == DKSEventState.TAP
        # All other states are OFF
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (1, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF

        # Exact roundtrip
        assert rec.to_bytes() == raw_exp_c

    def test_physical_action2_hold(self):
        # Final Control Test: Point 0 Action 2 HOLD -> byte +12 = 0x20
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 20 00 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[1][0] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (1, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_a1_action3_tap(self):
        # Microtest A1: Point 0 Action 3 TAP -> byte +12 = 0x04
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 04 00 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[2][0] == DKSEventState.TAP
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (2, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_a2_action3_hold(self):
        # Microtest A2: Point 0 Action 3 HOLD -> byte +12 = 0x40
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 40 00 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[2][0] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (2, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_b1_action4_tap(self):
        # Microtest B1: Point 0 Action 4 TAP -> byte +12 = 0x08
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 08 00 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[3][0] == DKSEventState.TAP
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (3, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_b2_action4_hold(self):
        # Microtest B2: Point 0 Action 4 HOLD -> byte +12 = 0x80
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 80 00 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[3][0] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (3, 0):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_c1_point1_tap(self):
        # Microtest C1: Point 1 Action 1 TAP -> byte +13 = 0x01
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 01 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][1] == DKSEventState.TAP
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 1):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_c2_point1_hold(self):
        # Microtest C2: Point 1 Action 1 HOLD -> byte +13 = 0x10
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 10 00 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][1] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 1):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_d1_point2_tap(self):
        # Microtest D1: Point 2 Action 1 TAP -> byte +14 = 0x01
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 00 01 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][2] == DKSEventState.TAP
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 2):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_d2_point2_hold(self):
        # Microtest D2: Point 2 Action 1 HOLD -> byte +14 = 0x10
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 00 10 00")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][2] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 2):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_e1_point3_tap(self):
        # Microtest E1: Point 3 Action 1 TAP -> byte +15 = 0x01
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 00 00 01")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][3] == DKSEventState.TAP
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 3):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw

    def test_physical_e2_point3_hold(self):
        # Microtest E2: Point 3 Action 1 HOLD -> byte +15 = 0x10
        raw = bytes.fromhex("0F 1E 1E 0F 00 1A 00 E1 00 00 00 00 00 00 00 10")
        rec = DKSRecord.from_bytes(raw)
        assert rec.states[0][3] == DKSEventState.HOLD
        for a_idx in range(4):
            for p_idx in range(4):
                if (a_idx, p_idx) != (0, 3):
                    assert rec.states[a_idx][p_idx] == DKSEventState.OFF
        assert rec.to_bytes() == raw


class TestDKSStructuralInference:
    """
    Tier 2: Structural inference tests for unverified scancode slots (Action 3 at +9, Action 4 at +11).
    State byte bitmasks and travel point offsets are now Tier 1 Physically Confirmed.
    """

    def test_inferred_action3_and_action4_tap_and_hold_point0(self):
        # Inductive mapping:
        # Action 3 TAP = low bit 2 -> 0x04; Action 3 HOLD = high bit 2 -> 0x40
        # Action 4 TAP = low bit 3 -> 0x08; Action 4 HOLD = high bit 3 -> 0x80
        # Test Action 3 TAP
        raw_act3_tap = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 15 00 00 04 00 00 00")
        rec3_tap = DKSRecord.from_bytes(raw_act3_tap)
        assert rec3_tap.actions[2] == 0x15
        assert rec3_tap.states[2][0] == DKSEventState.TAP
        assert rec3_tap.to_bytes() == raw_act3_tap

        # Test Action 3 HOLD
        raw_act3_hold = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 15 00 00 40 00 00 00")
        rec3_hold = DKSRecord.from_bytes(raw_act3_hold)
        assert rec3_hold.states[2][0] == DKSEventState.HOLD
        assert rec3_hold.to_bytes() == raw_act3_hold

        # Test Action 4 TAP
        raw_act4_tap = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 00 00 2C 08 00 00 00")
        rec4_tap = DKSRecord.from_bytes(raw_act4_tap)
        assert rec4_tap.actions[3] == 0x2C
        assert rec4_tap.states[3][0] == DKSEventState.TAP
        assert rec4_tap.to_bytes() == raw_act4_tap

        # Test Action 4 HOLD
        raw_act4_hold = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 00 00 2C 80 00 00 00")
        rec4_hold = DKSRecord.from_bytes(raw_act4_hold)
        assert rec4_hold.states[3][0] == DKSEventState.HOLD
        assert rec4_hold.to_bytes() == raw_act4_hold

    def test_inferred_points_1_to_3_state_byte_offsets(self):
        # Inductive mapping for travel points:
        # Point 0 -> byte +12
        # Point 1 (make2) -> byte +13
        # Point 2 (break1) -> byte +14
        # Point 3 (break2) -> byte +15
        for p_idx, byte_offset in enumerate([13, 14, 15], start=1):
            buf = bytearray(16)
            buf[0:4] = bytes([15, 30, 30, 15])
            buf[5] = 0x1A  # Action 1 = W
            buf[byte_offset] = 0x01  # Action 1 TAP at point p_idx
            raw = bytes(buf)

            rec = DKSRecord.from_bytes(raw)
            assert rec.states[0][p_idx] == DKSEventState.TAP
            assert rec.to_bytes() == raw


class TestDKSCollisionPrecedence:
    """
    Tier 3: Validates the currently documented Python decoder collision behavior.
    Synthetic state byte 0x11 has both TAP (low bit 0) and HOLD (high bit 0) set.
    Python decoder precedence: HOLD-before-TAP.
    NOTE: Does NOT claim physical validation of collision semantics.
    """

    def test_collision_precedence_hold_before_tap(self):
        # Synthetic byte at Point 0: 0x11 (Action 1 has both single bit 0 and hold bit 0 set)
        raw_collision = bytes.fromhex("0F 1E 1E 0F 00 1A 00 00 00 00 00 00 11 00 00 00")
        rec = DKSRecord.from_bytes(raw_collision)

        # Documented Python decoder precedence evaluates HOLD first:
        assert rec.states[0][0] == DKSEventState.HOLD

        # When re-encoded by to_bytes(), DKSEventState.HOLD serializes cleanly to 0x10
        # (the conflicting single bit is dropped, as expected for mutually-exclusive states)
        re_encoded = rec.to_bytes()
        assert re_encoded[12] == 0x10
