"""
Regression tests for Hall/RT wire format, dense addressing, and transport ACK filtering.

Verifies:
1. Stale ACK (e.g. 55 15) received before genuine ACK (55 27) is skipped within timeout deadline.
2. ACK filtering by opcode and address.
3. Foreign opcode is NEVER accepted as a valid ACK.
4. Canonical Hall record wire serialization and deserialization (<BBHHH).
5. Rapid Trigger enabled state strictly tied to flags bit 0 (flags & 0x01).
6. Dense physical Hall addressing (bank * 16 + col) * 8.
7. Hall read response payload begins at byte 8 (resp[8 : 8 + 56]).
"""

import struct
import unittest

from keyboard_re.models.base import (
    ACTUATION_OFFSET,
    BANK_HEADER_SIZE,
    KEY_MAP,
    KEY_RECORD_SIZE,
    KeyConfig,
    address_to_bank_col,
    key_address,
)
from keyboard_re.protocol.executor import (
    ExecutionStatus,
    ProfilePlanExecutor,
    validate_chunk_ack,
)
from keyboard_re.protocol.hall import (
    HALL_CHUNK_PAYLOAD_SIZE,
    HallKeyConfig,
    build_hall_chunk,
    decode_hall_record,
    encode_hall_record,
    parse_hall_chunk,
)
from keyboard_re.protocol.plan import ProfileWritePlan, SubsystemWriteStep, WriteChunk
from keyboard_re.protocol.read import parse_hall_read_chunks, read_hall_profile
from keyboard_re.protocol.transport import MockHidTransport


class TestTransportStaleAckFiltering(unittest.TestCase):
    """Tests for native_hid / MockHidTransport and executor stale ACK handling."""

    def test_stale_ack_before_genuine_ack_is_skipped(self):
        """
        When a stale ACK (e.g. 55 15) sits in the queue before the genuine ACK (55 27),
        transport.receive_report(expected_opcode=0x27) must discard the stale report and return the 55 27.
        """
        transport = MockHidTransport(auto_ack=False)

        # Queue stale 55 15 report (e.g. from previous GET_MACRO read)
        stale_ack = bytearray(64)
        stale_ack[0] = 0x55
        stale_ack[1] = 0x15
        stale_ack[2] = 0x38
        struct.pack_into("<H", stale_ack, 3, 0x0000)
        transport.queue_response(bytes(stale_ack))

        # Queue genuine 55 27 report
        genuine_ack = bytearray(64)
        genuine_ack[0] = 0x55
        genuine_ack[1] = 0x27
        genuine_ack[2] = 0x38
        struct.pack_into("<H", genuine_ack, 3, 0x0000)
        transport.queue_response(bytes(genuine_ack))

        # Request report expecting opcode 0x27, address 0x0000
        rep = transport.receive_report(timeout=1.0, expected_opcode=0x27, expected_address=0x0000)
        self.assertEqual(rep[0], 0x55)
        self.assertEqual(rep[1], 0x27)
        self.assertEqual(struct.unpack_from("<H", rep, 3)[0], 0x0000)

    def test_stale_address_mismatch_is_skipped(self):
        """
        When an ACK with same opcode but wrong address is queued, it is skipped
        until the matching address arrives.
        """
        transport = MockHidTransport(auto_ack=False)

        # Stale ACK for chunk addr 0x0038
        stale_ack = bytearray(64)
        stale_ack[0] = 0x55
        stale_ack[1] = 0x27
        stale_ack[2] = 0x38
        struct.pack_into("<H", stale_ack, 3, 0x0038)
        transport.queue_response(bytes(stale_ack))

        # Real ACK for chunk addr 0x0000
        real_ack = bytearray(64)
        real_ack[0] = 0x55
        real_ack[1] = 0x27
        real_ack[2] = 0x38
        struct.pack_into("<H", real_ack, 3, 0x0000)
        transport.queue_response(bytes(real_ack))

        rep = transport.receive_report(timeout=1.0, expected_opcode=0x27, expected_address=0x0000)
        self.assertEqual(struct.unpack_from("<H", rep, 3)[0], 0x0000)

    def test_foreign_opcode_never_accepted_as_valid_ack(self):
        """
        validate_chunk_ack strictly rejects any report whose opcode does not match expected_ack opcode.
        """
        chunk = WriteChunk(
            chunk_index=0,
            address=0,
            size=56,
            packet=bytes(64),
            expected_ack=bytes([0x55, 0x27, 56, 0, 0] + [0] * 59),
        )

        foreign_ack = bytearray(64)
        foreign_ack[0] = 0x55
        foreign_ack[1] = 0x15  # GET_MACRO opcode
        foreign_ack[2] = 56
        struct.pack_into("<H", foreign_ack, 3, 0)

        err = validate_chunk_ack(chunk, bytes(foreign_ack), strict_payload=False)
        self.assertIsNotNone(err)
        self.assertIn("Invalid ACK opcode 0x15, expected 0x27", err)

    def test_executor_drain_and_stale_ack_skipping(self):
        """
        ProfilePlanExecutor drains buffer before write and skips stale ACK during chunk wait.
        """
        transport = MockHidTransport(auto_ack=False)

        # Add pre-existing stale report that should be drained
        leftover = bytes([0x55, 0x10, 0x30] + [0] * 61)
        transport.queue_response(leftover)

        # Plan with 1 chunk
        pkt = bytearray(64)
        pkt[0] = 0xAA
        pkt[1] = 0x27
        pkt[2] = 56
        struct.pack_into("<H", pkt, 3, 0)

        exp_ack = bytearray(64)
        exp_ack[0] = 0x55
        exp_ack[1] = 0x27
        exp_ack[2] = 56
        struct.pack_into("<H", exp_ack, 3, 0)

        chunk = WriteChunk(chunk_index=0, address=0, size=56, packet=bytes(pkt), expected_ack=bytes(exp_ack))
        step = SubsystemWriteStep(subsystem="hall", opcode=0x27, description="Hall Write", chunks=[chunk])
        plan = ProfileWritePlan(profile_id=1, profile_name="Profile 1", steps=[step])

        # Even if a stale report arrives after send, queue a stale 55 15 followed by 55 27
        stale = bytes([0x55, 0x15, 56, 0, 0] + [0] * 59)
        valid = bytes(exp_ack)

        orig_send = transport.send_report
        def on_send(report_id, data):
            orig_send(report_id, data)
            transport.queue_response(stale)
            transport.queue_response(valid)
        transport.send_report = on_send

        executor = ProfilePlanExecutor(transport=transport, timeout=1.0, strict_payload=False)
        from keyboard_re.models.state import Profile, HallProfileState
        from keyboard_re.protocol.keymap import KeymapTable
        mock_prof = Profile(
            profile_id=1,
            name="Profile 1",
            hall=HallProfileState.from_bytes(bytes(1008)),
            remap=KeymapTable(layer=1, slots={}, raw_bytes=bytes(512)),
        )

        res = executor.execute(plan, target_profile=mock_prof, verify_readback=False)
        self.assertTrue(res.is_success)
        self.assertEqual(res.acks_received, 1)


class TestHallWireFormatCanonical(unittest.TestCase):
    """Tests canonical <BBHHH Hall layout and Rapid Trigger bit flag."""

    def test_hall_record_wire_packing(self):
        """
        Canonical layout: <BBHHH
        +0: axis_type (uint8)
        +1: flags (uint8, bit 0 = RT)
        +2..3: actuation (uint16 LE)
        +4..5: rt_press (uint16 LE)
        +6..7: rt_release (uint16 LE)
        """
        cfg = HallKeyConfig(
            axis_type=1,
            flags=0x01,
            actuation_mm=1.40,
            rt_press_mm=0.20,
            rt_release_mm=0.10,
        )
        raw = cfg.to_bytes()
        self.assertEqual(len(raw), 8)

        expected = struct.pack("<BBHHH", 1, 1, 140, 20, 10)
        self.assertEqual(raw, expected)

        # Unpack directly
        unpacked = struct.unpack("<BBHHH", raw)
        self.assertEqual(unpacked, (1, 1, 140, 20, 10))

        # Decode round-trip
        decoded = HallKeyConfig.from_bytes(raw)
        self.assertEqual(decoded.axis_type, 1)
        self.assertEqual(decoded.flags, 1)
        self.assertEqual(decoded.actuation_mm, 1.40)
        self.assertEqual(decoded.rt_press_mm, 0.20)
        self.assertEqual(decoded.rt_release_mm, 0.10)
        self.assertTrue(decoded.is_rt_enabled)

    def test_rt_flag_bit0_semantics(self):
        """Rapid Trigger enable status is strictly flags & 0x01."""
        cfg = HallKeyConfig(flags=0x00, rt_press_mm=0.20, rt_release_mm=0.20)
        self.assertFalse(cfg.is_rt_enabled, "Even with non-zero sensitivities, RT is OFF if bit 0 is 0")

        cfg.is_rt_enabled = True
        self.assertTrue(cfg.is_rt_enabled)
        self.assertEqual(cfg.flags & 0x01, 1)

        cfg.disable_rt()
        self.assertFalse(cfg.is_rt_enabled)
        self.assertEqual(cfg.flags & 0x01, 0)

        cfg.enable_rt(press_mm=0.30, release_mm=0.15)
        self.assertTrue(cfg.is_rt_enabled)
        self.assertEqual(cfg.flags & 0x01, 1)
        self.assertEqual(cfg.rt_press_mm, 0.30)
        self.assertEqual(cfg.rt_release_mm, 0.15)

    def test_keyconfig_interoperability(self):
        """KeyConfig subclass maintains complete fidelity with HallKeyConfig."""
        kc = KeyConfig(actuation=140, rt_press=20, rt_release=10, flags=1, axis_type=1)
        self.assertEqual(kc.to_bytes(), struct.pack("<BBHHH", 1, 1, 140, 20, 10))
        self.assertTrue(kc.is_rt_enabled)


class TestHallAddressingAndPayloadSlicing(unittest.TestCase):
    """Tests dense physical addressing and byte 8 payload slicing."""

    def test_dense_physical_addressing_formula(self):
        """Formula: (bank * 16 + col) * 8. Zero bank header."""
        self.assertEqual(BANK_HEADER_SIZE, 0)
        self.assertEqual(ACTUATION_OFFSET, 2)

        # Bank 0, Col 0 (ESC): 0 * 8 = 0
        self.assertEqual(key_address(0, 0), 0)
        self.assertEqual(address_to_bank_col(0), (0, 0))

        # Bank 2, Col 1 (Q): (2 * 16 + 1) * 8 = 33 * 8 = 264 (0x0108)
        self.assertEqual(key_address(2, 1), 264)
        self.assertEqual(address_to_bank_col(264), (2, 1))

        # Bank 3, Col 1 (A): (3 * 16 + 1) * 8 = 49 * 8 = 392 (0x0188)
        self.assertEqual(key_address(3, 1), 392)
        self.assertEqual(address_to_bank_col(392), (3, 1))

        # Bank 3, Col 2 (S): (3 * 16 + 2) * 8 = 50 * 8 = 400 (0x0190)
        self.assertEqual(key_address(3, 2), 400)
        self.assertEqual(address_to_bank_col(400), (3, 2))

    def test_hall_chunk_builder_and_parser_payload_at_byte_8(self):
        """Chunk packet: header bytes 0..7, payload bytes 8..63."""
        payload = bytes([i % 256 for i in range(56)])
        pkt = build_hall_chunk(address=56, payload=payload)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[:3], b"\xAA\x27\x38")
        self.assertEqual(struct.unpack_from("<H", pkt, 3)[0], 56)
        self.assertEqual(pkt[5:8], b"\x00\x00\x00", "Header bytes 5..7 must be zero")
        self.assertEqual(pkt[8:64], payload, "Payload must start at byte 8")

        addr, parsed_pl = parse_hall_chunk(pkt)
        self.assertEqual(addr, 56)
        self.assertEqual(parsed_pl, payload)

    def test_parse_hall_read_chunks_payload_at_byte_8(self):
        """parse_hall_read_chunks slices rep[8 : 8 + 56]."""
        reports = []
        full_image = bytearray(1008)
        for i in range(18):
            rep = bytearray(64)
            rep[0] = 0x55
            rep[1] = 0x17
            rep[2] = 0x38
            addr = i * 56
            struct.pack_into("<H", rep, 3, addr)
            chunk_data = bytes([(i * 56 + b) % 256 for b in range(56)])
            rep[8:64] = chunk_data
            full_image[addr : addr + 56] = chunk_data
            reports.append(bytes(rep))

        reconstructed = parse_hall_read_chunks(reports)
        self.assertEqual(len(reconstructed), 1008)
        self.assertEqual(reconstructed, bytes(full_image))


if __name__ == "__main__":
    unittest.main()
