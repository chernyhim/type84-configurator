"""
Unit tests for production Key Remap L1 (AA 22) Write Pipeline.

Covers:
1. 128 slots x 4 bytes = 512 dimensions and constants.
2. Slot offsets (0 -> 0, 1 -> 4, 50 -> 200, 126 -> 504, 127 -> 508).
3. Record A and B encode/decode.
4. Diff calculation (Key A -> B single byte diff at offset 201).
5. Packet builder for address 0, 200, tail address 504.
6. Packet builder error handling.
7. ACK parser and validator.
8. ACK mismatch rejection (wrong opcode, address, size, payload echo).
9. Exact 10 chunk generation and sequencing.
10. Image round-trip serialization/deserialization.
11. patch_remap_image byte preservation on all boundaries (0, 1, 50, 126, 127).
12. Safety policy gate rejection.
13. Empty diff early-out (0 writes).
14. Read-before-write mismatch abort.
15. Read-back mismatch failure.
16. Full end-to-end closed-loop write transaction success.
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple
import unittest

from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.remap import (
    REMAP_CHUNK_COUNT,
    REMAP_CHUNK_SIZE,
    REMAP_IMAGE_SIZE,
    REMAP_READ_OPCODE,
    REMAP_SLOT_COUNT,
    REMAP_SLOT_SIZE,
    REMAP_TAIL_SIZE,
    REMAP_WRITE_OPCODE,
    RemapAck,
    RemapAckError,
    RemapByteDiff,
    RemapRecord,
    RemapWritePolicy,
    RemapWriteResult,
    build_image,
    build_remap_write_chunks,
    build_remap_write_packet,
    decode_record,
    diff_images,
    encode_record,
    parse_image,
    parse_remap_ack,
    patch_remap_image,
    slot_offset,
    validate_remap_ack,
    write_remap_image,
)
from keyboard_re.protocol.transport import HidTransport


class SimulatedKeymapDevice(HidTransport):
    """
    In-memory simulation of the Type 84 keyboard firmware for Remap L1.
    
    Supports:
    - AA 12 read queries (10 chunks -> 55 12 responses)
    - AA 22 write queries (10 chunks -> memory update -> 55 22 ACKs)
    - Fault injection: corrupted readback, corrupted ACK, corrupted baseline
    """

    def __init__(self, initial_image: bytes):
        self.memory = bytearray(initial_image)
        self.sent_packets: List[Tuple[int, bytes]] = []
        self.response_queue: List[bytes] = []
        self.corrupt_ack_opcode: bool = False
        self.corrupt_ack_address: bool = False
        self.corrupt_ack_payload: bool = False
        self.corrupt_readback: bool = False
        self.writes_received: int = 0
        self._is_open = True

    @property
    def is_connected(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def send_report(self, report_id: int, data: bytes) -> None:
        if not self._is_open:
            raise ConnectionError("Device closed")
        self.sent_packets.append((report_id, bytes(data)))
        req = bytes(data)

        # Handle READ (AA 12)
        if req.startswith(b"\xAA\x12"):
            sz = req[2]
            addr = struct.unpack_from("<H", req, 3)[0]
            resp = bytearray(REPORT_SIZE)
            resp[0] = 0x55
            resp[1] = 0x12
            resp[2] = sz
            struct.pack_into("<H", resp, 3, addr)
            resp[6] = req[6]
            payload = self.memory[addr : addr + sz]
            if self.corrupt_readback and self.writes_received > 0 and addr == 0:
                payload = bytearray(payload)
                payload[0] ^= 0xFF
            resp[8 : 8 + sz] = payload
            self.response_queue.append(bytes(resp))

        # Handle WRITE (AA 22)
        elif req.startswith(b"\xAA\x22"):
            self.writes_received += 1
            sz = req[2]

            addr = struct.unpack_from("<H", req, 3)[0]
            payload = req[8 : 8 + sz]
            self.memory[addr : addr + sz] = payload

            ack = bytearray(REPORT_SIZE)
            ack[0] = 0x55
            ack[1] = 0x99 if self.corrupt_ack_opcode else 0x22
            ack[2] = sz
            ack_addr = (addr + 1) if self.corrupt_ack_address else addr
            struct.pack_into("<H", ack, 3, ack_addr)
            ack[6] = req[6]
            ack_payload = bytearray(payload)
            if self.corrupt_ack_payload:
                ack_payload[0] ^= 0xFF
            ack[8 : 8 + sz] = ack_payload
            self.response_queue.append(bytes(ack))
        else:
            # Default echo
            echo = bytearray(64)
            echo[0] = 0x55
            echo[1] = req[1]
            echo[2] = req[2]
            echo[3:5] = req[3:5]
            self.response_queue.append(bytes(echo))

    def receive_report(self, timeout: float = 1.0) -> bytes:
        if self.response_queue:
            return self.response_queue.pop(0)
        raise TimeoutError("No responses queued in simulated device")

    def write_report(self, data: bytes, report_id: int = 0, timeout_ms: int = 1000) -> None:
        self.send_report(report_id, data)

    def read_report(self, timeout_ms: int = 1000) -> Optional[bytes]:
        try:
            return self.receive_report(timeout=timeout_ms / 1000.0)
        except TimeoutError:
            return None


class TestRemapWritePipeline(unittest.TestCase):
    def setUp(self):
        # Create a standard 512-byte baseline where Key A is at slot 50 (offset 200)
        self.baseline = bytearray(REMAP_IMAGE_SIZE)
        # Esc at slot 1: 00 29 00 02
        self.baseline[4:8] = bytes([0x00, 0x29, 0x00, 0x02])
        # Key A at slot 50: 00 04 00 02
        self.baseline[200:204] = bytes([0x00, 0x04, 0x00, 0x02])
        # Activation marker at slot 126: 00 01 00 00
        self.baseline[504:508] = bytes([0x00, 0x01, 0x00, 0x00])

    # 1. 128 slots x 4 bytes = 512
    def test_dimensions_and_constants(self):
        self.assertEqual(REMAP_SLOT_COUNT * REMAP_SLOT_SIZE, REMAP_IMAGE_SIZE)
        self.assertEqual(REMAP_IMAGE_SIZE, 512)
        self.assertEqual(REMAP_SLOT_COUNT, 128)
        self.assertEqual(REMAP_SLOT_SIZE, 4)
        self.assertEqual(REMAP_WRITE_OPCODE, 0x22)
        self.assertEqual(REMAP_READ_OPCODE, 0x12)
        self.assertEqual(REMAP_CHUNK_SIZE, 56)
        self.assertEqual(REMAP_TAIL_SIZE, 8)
        self.assertEqual(REMAP_CHUNK_COUNT, 10)
        self.assertEqual(9 * 56 + 1 * 8, 512)

    # 2. Slot offsets on boundaries and key slots
    def test_slot_offsets(self):
        self.assertEqual(slot_offset(0), 0)
        self.assertEqual(slot_offset(1), 4)
        self.assertEqual(slot_offset(50), 200)
        self.assertEqual(slot_offset(126), 504)
        self.assertEqual(slot_offset(127), 508)

        # Boundary checks
        with self.assertRaises(ValueError):
            slot_offset(-1)
        with self.assertRaises(ValueError):
            slot_offset(128)

    # 3. Record A encode/decode
    def test_record_a_encode_decode(self):
        rec_a = RemapRecord(prefix=0x00, scancode=0x04, special=0x00, type=0x02)
        raw_a = encode_record(rec_a)
        self.assertEqual(raw_a, bytes([0x00, 0x04, 0x00, 0x02]))
        self.assertEqual(rec_a.to_bytes(), raw_a)

        decoded = decode_record(raw_a)
        self.assertEqual(decoded, rec_a)
        self.assertEqual(decoded.prefix, 0)
        self.assertEqual(decoded.scancode, 0x04)
        self.assertEqual(decoded.special, 0)
        self.assertEqual(decoded.type, 0x02)
        self.assertEqual(decoded.function_type, 0x02)
        self.assertFalse(decoded.is_empty)

    # 4. Record B encode/decode
    def test_record_b_encode_decode(self):
        rec_b = RemapRecord(prefix=0x00, scancode=0x05, special=0x00, type=0x02)
        raw_b = encode_record(rec_b)
        self.assertEqual(raw_b, bytes([0x00, 0x05, 0x00, 0x02]))

        decoded = decode_record(raw_b)
        self.assertEqual(decoded, rec_b)
        self.assertEqual(decoded.scancode, 0x05)

    # 5. Empty slot record
    def test_record_empty(self):
        empty_rec = RemapRecord(prefix=0, scancode=0, special=0, type=0)
        self.assertTrue(empty_rec.is_empty)
        self.assertEqual(encode_record(empty_rec), bytes(4))

    # 6. A -> B diff isolation
    def test_a_to_b_diff(self):
        before = bytes(self.baseline)
        after = patch_remap_image(
            before,
            slot=50,
            new_record=RemapRecord(prefix=0, scancode=0x05, special=0, type=0x02),
        )
        diffs = diff_images(before, after)
        self.assertEqual(len(diffs), 1)
        d = diffs[0]
        self.assertEqual(d.offset, 201)
        self.assertEqual(d.before, 0x04)
        self.assertEqual(d.after, 0x05)
        self.assertEqual(d.slot, 50)
        self.assertEqual(d.slot_byte, 1)

    # 7. Packet builder for address 0
    def test_packet_builder_address_0(self):
        payload = bytes(range(56))
        pkt = build_remap_write_packet(0, payload)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[0], 0xAA)
        self.assertEqual(pkt[1], 0x22)
        self.assertEqual(pkt[2], 56)
        self.assertEqual(struct.unpack_from("<H", pkt, 3)[0], 0)
        self.assertEqual(pkt[8 : 8 + 56], payload)

    # 8. Packet builder for address 200
    def test_packet_builder_address_200(self):
        payload = bytes([0xAA] * 56)
        pkt = build_remap_write_packet(200, payload)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[0:3], bytes([0xAA, 0x22, 56]))
        self.assertEqual(struct.unpack_from("<H", pkt, 3)[0], 200)
        self.assertEqual(pkt[8 : 8 + 56], payload)

    # 9. Packet builder for tail address 504
    def test_packet_builder_tail_504(self):
        tail_payload = bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        pkt = build_remap_write_packet(504, tail_payload, is_last=True)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[0:3], bytes([0xAA, 0x22, 8]))
        self.assertEqual(struct.unpack_from("<H", pkt, 3)[0], 504)
        self.assertEqual(pkt[6], 0x01)
        self.assertEqual(pkt[8 : 8 + 8], tail_payload)

    # 10. Packet builder invalid bounds
    def test_packet_builder_invalid(self):
        # Empty payload
        with self.assertRaises(ValueError):
            build_remap_write_packet(0, b"")
        # Oversized payload
        with self.assertRaises(ValueError):
            build_remap_write_packet(0, bytes(57))
        # Address + len overflow
        with self.assertRaises(ValueError):
            build_remap_write_packet(505, bytes(8))  # 505 + 8 = 513 > 512
        with self.assertRaises(ValueError):
            build_remap_write_packet(-1, bytes(56))

    # 11. ACK parser
    def test_ack_parser_valid(self):
        report = bytearray(64)
        report[0] = 0x55
        report[1] = 0x22
        report[2] = 56
        struct.pack_into("<H", report, 3, 112)
        payload = bytes(range(56))
        report[8 : 8 + 56] = payload

        ack = parse_remap_ack(bytes(report))
        self.assertEqual(ack.opcode, 0x22)
        self.assertEqual(ack.size, 56)
        self.assertEqual(ack.address, 112)
        self.assertEqual(ack.payload_echo, payload)

    # 12. ACK mismatch rejection (wrong opcode, address, size, payload)
    def test_ack_mismatch_rejection(self):
        valid_payload = bytes(range(56))
        ack_pkt = bytearray(64)
        ack_pkt[0] = 0x55
        ack_pkt[1] = 0x22
        ack_pkt[2] = 56
        struct.pack_into("<H", ack_pkt, 3, 0)
        ack_pkt[8 : 8 + 56] = valid_payload
        valid_ack = bytes(ack_pkt)

        # 1. Valid case
        self.assertTrue(validate_remap_ack(0, valid_payload, valid_ack))

        # 2. Wrong prefix
        bad_prefix = bytearray(valid_ack)
        bad_prefix[0] = 0xAA
        with self.assertRaises(RemapAckError):
            validate_remap_ack(0, valid_payload, bytes(bad_prefix))

        # 3. Wrong opcode
        bad_opcode = bytearray(valid_ack)
        bad_opcode[1] = 0x27
        with self.assertRaises(RemapAckError):
            validate_remap_ack(0, valid_payload, bytes(bad_opcode))

        # 4. Wrong address
        with self.assertRaises(RemapAckError):
            validate_remap_ack(56, valid_payload, valid_ack)

        # 5. Wrong size
        with self.assertRaises(RemapAckError):
            validate_remap_ack(0, bytes(range(8)), valid_ack)

        # 6. Wrong payload echo
        corrupted_payload = bytearray(valid_payload)
        corrupted_payload[10] ^= 0xFF
        with self.assertRaises(RemapAckError):
            validate_remap_ack(0, bytes(corrupted_payload), valid_ack)

    # 13. Exact 10 chunk construction
    def test_exact_10_chunks(self):
        chunks = build_remap_write_chunks(bytes(self.baseline))
        self.assertEqual(len(chunks), 10)

        expected_addrs = [0, 56, 112, 168, 224, 280, 336, 392, 448, 504]
        for i, (addr, payload, pkt) in enumerate(chunks):
            self.assertEqual(addr, expected_addrs[i])
            self.assertEqual(len(pkt), 64)
            if i < 9:
                self.assertEqual(len(payload), 56)
                self.assertEqual(pkt[2], 56)
            else:
                self.assertEqual(len(payload), 8)
                self.assertEqual(pkt[2], 8)

    # 14. Image round-trip
    def test_image_round_trip(self):
        records = [
            RemapRecord(prefix=i % 4, scancode=(i * 3) % 256, special=0, type=0x02)
            for i in range(128)
        ]
        image = build_image(records)
        self.assertEqual(len(image), 512)

        parsed = parse_image(image)
        self.assertEqual(len(parsed), 128)
        self.assertEqual(parsed, records)

        rebuilt = build_image(parsed)
        self.assertEqual(rebuilt, image)

    # 15. patch_remap_image boundary tests (slots 0, 1, 50, 126, 127)
    def test_patch_remap_image_boundaries(self):
        base = bytes(self.baseline)

        # Slot 0
        p0 = patch_remap_image(base, 0, RemapRecord(prefix=1, scancode=2, special=3, type=4))
        d0 = diff_images(base, p0)
        self.assertTrue(all(d.slot == 0 for d in d0))
        self.assertEqual(len(d0), 4)

        # Slot 1
        p1 = patch_remap_image(base, 1, RemapRecord(prefix=0, scancode=0x3A, special=0, type=0x02))
        d1 = diff_images(base, p1)
        self.assertTrue(all(d.slot == 1 for d in d1))

        # Slot 50
        p50 = patch_remap_image(base, 50, RemapRecord(prefix=0, scancode=0x05, special=0, type=0x02))
        d50 = diff_images(base, p50)
        self.assertEqual(len(d50), 1)
        self.assertEqual(d50[0].slot, 50)
        self.assertEqual(d50[0].offset, 201)

        # Slot 126
        p126 = patch_remap_image(base, 126, RemapRecord(prefix=0, scancode=0, special=0, type=0))
        d126 = diff_images(base, p126)
        self.assertTrue(all(d.slot == 126 for d in d126))
        self.assertEqual(d126[0].offset, 505)

        # Slot 127 (last slot, offset 508..511)
        p127 = patch_remap_image(base, 127, RemapRecord(prefix=9, scancode=8, special=7, type=6))
        d127 = diff_images(base, p127)
        self.assertTrue(all(d.slot == 127 for d in d127))
        self.assertEqual([d.offset for d in d127], [508, 509, 510, 511])

    # 16. Safety policy gate
    def test_safety_policy_unconfirmed_rejected(self):
        dev = SimulatedKeymapDevice(bytes(self.baseline))
        target = patch_remap_image(
            bytes(self.baseline), 50, RemapRecord(0, 5, 0, 2)
        )
        # Default policy requires confirmed=True
        with self.assertRaises(PermissionError):
            write_remap_image(dev, target, baseline=bytes(self.baseline))

        policy = RemapWritePolicy(confirmed=False)
        with self.assertRaises(PermissionError):
            write_remap_image(dev, target, baseline=bytes(self.baseline), policy=policy)

    # 17. Empty diff causes zero writes
    def test_empty_diff_causes_no_write(self):
        dev = SimulatedKeymapDevice(bytes(self.baseline))
        policy = RemapWritePolicy(confirmed=True)
        res = write_remap_image(
            dev,
            image=bytes(self.baseline),
            baseline=bytes(self.baseline),
            policy=policy,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.chunks_sent, 0)
        self.assertEqual(res.acks_received, 0)
        self.assertEqual(len(res.changed_bytes), 0)
        self.assertEqual(len(res.changed_slots), 0)
        self.assertTrue(res.readback_verified)
        self.assertEqual(len(dev.sent_packets), 0)

    # 18. Read-before-write mismatch aborts before write
    def test_read_before_write_mismatch_aborts(self):
        # Baseline supplied by caller differs from device state
        dev = SimulatedKeymapDevice(bytes(self.baseline))
        different_baseline = bytearray(self.baseline)
        different_baseline[10] ^= 0xFF

        target = patch_remap_image(
            different_baseline, 50, RemapRecord(0, 5, 0, 2)
        )
        policy = RemapWritePolicy(confirmed=True)

        with self.assertRaises(RuntimeError) as ctx:
            write_remap_image(
                dev,
                image=target,
                baseline=bytes(different_baseline),
                policy=policy,
            )
        self.assertIn("Read-before-write mismatch", str(ctx.exception))
        # Ensure zero write reports were sent
        write_reports = [p for p in dev.sent_packets if p[1].startswith(b"\xAA\x22")]
        self.assertEqual(len(write_reports), 0)

    # 19. Read-back mismatch causes failure
    def test_readback_mismatch_fails(self):
        dev = SimulatedKeymapDevice(bytes(self.baseline))
        dev.corrupt_readback = True
        target = patch_remap_image(
            bytes(self.baseline), 50, RemapRecord(0, 5, 0, 2)
        )
        policy = RemapWritePolicy(confirmed=True, require_readback=True)

        with self.assertRaises(RuntimeError) as ctx:
            write_remap_image(
                dev,
                image=target,
                baseline=bytes(self.baseline),
                policy=policy,
            )
        self.assertIn("Read-back verification FAILURE", str(ctx.exception))

    # 20. End-to-end successful write transaction
    def test_full_write_transaction_success(self):
        dev = SimulatedKeymapDevice(bytes(self.baseline))
        target = patch_remap_image(
            bytes(self.baseline), 50, RemapRecord(0, 5, 0, 2)
        )
        policy = RemapWritePolicy(confirmed=True)

        res = write_remap_image(
            dev,
            image=target,
            baseline=bytes(self.baseline),
            policy=policy,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.chunks_sent, 10)
        self.assertEqual(res.acks_received, 10)
        self.assertTrue(res.readback_verified)
        self.assertEqual(res.changed_slots, [50])
        self.assertEqual(len(res.changed_bytes), 1)
        self.assertEqual(res.changed_bytes[0].offset, 201)
        self.assertEqual(res.changed_bytes[0].before, 0x04)
        self.assertEqual(res.changed_bytes[0].after, 0x05)

        # Device memory updated
        self.assertEqual(dev.memory[200:204], bytes([0x00, 0x05, 0x00, 0x02]))


if __name__ == "__main__":
    unittest.main()
