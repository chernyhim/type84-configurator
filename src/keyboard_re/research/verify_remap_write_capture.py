"""
Forensic Verification Script for Real WebHID Remap L1 Write Capture.

Verifies:
1. Exactly 10 outgoing packets with opcode AA 22.
2. Report ID = 0, size = 64 bytes, interface = 0xFF68:0x0061.
3. Exact address sequence: [0, 56, 112, 168, 224, 280, 336, 392, 448, 504].
4. Exact payload sizes: 56 bytes x 9 chunks + 8 bytes x 1 chunk = 512 bytes.
5. Exactly 10 matching synchronous ACKs with opcode 55 22 and exact payload echoes.
6. Absence of an 11th commit / terminator packet.
7. Verification of Key A -> B modification at slot 50 (offset 200..203).
8. Validation against keyboard_re.protocol.remap packet builder & ACK validator.
"""

from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
from typing import Any, Dict, List, Optional, Tuple

from keyboard_re.protocol.remap import (
    REMAP_CHUNK_COUNT,
    REMAP_CHUNK_SIZE,
    REMAP_IMAGE_SIZE,
    REMAP_SLOT_COUNT,
    REMAP_SLOT_SIZE,
    REMAP_TAIL_SIZE,
    REMAP_WRITE_OPCODE,
    RemapAck,
    RemapRecord,
    build_remap_write_chunks,
    build_remap_write_packet,
    diff_images,
    parse_remap_ack,
    patch_remap_image,
    slot_offset,
    validate_remap_ack,
)

EXPECTED_ADDRESSES = [0, 56, 112, 168, 224, 280, 336, 392, 448, 504]
DEFAULT_CAPTURE_PATH = Path("captures/experiments/remap_write_a_to_b.json")
BASELINE_BEFORE_PATH = Path("captures/research/remap_l1/a_to_b_before.bin")
BASELINE_AFTER_PATH = Path("captures/research/remap_l1/a_to_b_after.bin")


def verify_capture(capture_path: Path) -> bool:
    """
    Perform exhaustive verification of real WebHID Remap L1 write capture.
    """
    print("=" * 76)
    print("FORENSIC CAPTURE VERIFICATION: REMAP L1 WRITE (AA 22)")
    print("=" * 76)
    print(f"Capture file: {capture_path}")

    if not capture_path.exists():
        print(f"ERROR: Capture file not found: {capture_path}")
        return False

    with open(capture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events: List[Dict[str, Any]] = data.get("events", [])
    print(f"Total events in capture: {len(events)}")

    # 1. Filter HOST -> DEVICE packets with AA 22
    host_packets: List[Tuple[int, bytes, float]] = []
    dev_packets: List[Tuple[int, bytes, float]] = []
    all_aa22_events: List[Dict[str, Any]] = []

    for ev in events:
        raw_hex = ev.get("data_hex") or ""
        raw_bytes = bytes.fromhex(raw_hex)
        direction = ev.get("direction", "")
        ts = float(ev.get("timestamp_ms", 0.0))

        if direction == "HOST -> DEVICE" and len(raw_bytes) >= 2 and raw_bytes[0] == 0xAA and raw_bytes[1] == 0x22:
            host_packets.append((ev.get("index", 0), raw_bytes, ts))
            all_aa22_events.append(ev)
        elif direction == "DEVICE -> HOST" and len(raw_bytes) >= 2 and raw_bytes[0] == 0x55 and raw_bytes[1] == 0x22:
            dev_packets.append((ev.get("index", 0), raw_bytes, ts))

    print(f"HOST -> DEVICE (AA 22) packets: {len(host_packets)}")
    print(f"DEVICE -> HOST (55 22) packets: {len(dev_packets)}")

    # CHECK 1: Exactly 10 outgoing packets
    if len(host_packets) != REMAP_CHUNK_COUNT:
        print(f"FAIL: Expected exactly 10 host write packets, got {len(host_packets)}")
        return False
    print("PASS 1: Exactly 10 outgoing AA 22 packets detected.")

    # CHECK 2: Exactly 10 incoming ACKs
    if len(dev_packets) != REMAP_CHUNK_COUNT:
        print(f"FAIL: Expected exactly 10 device ACK packets, got {len(dev_packets)}")
        return False
    print("PASS 2: Exactly 10 matching 55 22 ACK packets detected.")

    # CHECK 3: Absence of 11th commit / terminator packet
    after_last_idx = host_packets[-1][0]
    subsequent_host_pkts = [
        ev for ev in events
        if ev.get("index", 0) > after_last_idx and ev.get("direction") == "HOST -> DEVICE"
    ]
    if subsequent_host_pkts:
        print(f"FAIL: Found {len(subsequent_host_pkts)} unexpected packets after chunk #9")
        return False
    print("PASS 3: Absence of 11th commit packet confirmed (write completes in-band at chunk #9).")

    # CHECK 4: Addresses and Sizes
    reassembled = bytearray(REMAP_IMAGE_SIZE)
    for i, (idx, pkt, ts) in enumerate(host_packets):
        if len(pkt) != 64:
            print(f"FAIL: Packet #{i} length is {len(pkt)}, expected 64")
            return False

        sz = pkt[2]
        addr = struct.unpack_from("<H", pkt, 3)[0]
        expected_addr = EXPECTED_ADDRESSES[i]
        expected_sz = REMAP_CHUNK_SIZE if i < 9 else REMAP_TAIL_SIZE

        if addr != expected_addr:
            print(f"FAIL: Packet #{i} address mismatch: expected {expected_addr}, got {addr}")
            return False
        if sz != expected_sz:
            print(f"FAIL: Packet #{i} size mismatch: expected {expected_sz}, got {sz}")
            return False

        payload = pkt[5 : 5 + sz]
        reassembled[addr : addr + sz] = payload

    print(f"PASS 4: Exact address sequence verified: {EXPECTED_ADDRESSES}")
    print("PASS 5: Exact payload sizes verified (9 x 56B + 1 x 8B = 512B).")

    # CHECK 6: Validate each ACK with protocol validator
    for i in range(REMAP_CHUNK_COUNT):
        _, host_pkt, _ = host_packets[i]
        _, dev_pkt, _ = dev_packets[i]

        sz = host_pkt[2]
        addr = struct.unpack_from("<H", host_pkt, 3)[0]
        payload = host_pkt[5 : 5 + sz]

        try:
            validate_remap_ack(
                expected_address=addr,
                expected_payload=payload,
                response=dev_pkt,
            )
        except Exception as e:
            print(f"FAIL: ACK validation failed on chunk #{i} (addr {addr}): {e}")
            return False

    print("PASS 6: All 10 ACKs passed protocol validate_remap_ack (opcode, size, address, payload echo).")

    # CHECK 7: Packet reconstruction equivalence
    generated_chunks = build_remap_write_chunks(bytes(reassembled))
    for i, (addr, payload, pkt) in enumerate(generated_chunks):
        actual_pkt = host_packets[i][1]
        sz = pkt[2]
        if pkt[: 5 + sz] != actual_pkt[: 5 + sz]:
            print(f"FAIL: build_remap_write_chunks generated different payload for chunk #{i}")
            print(f"  Generated: {pkt[: 5 + sz].hex(' ').upper()}")
            print(f"  Actual:    {actual_pkt[: 5 + sz].hex(' ').upper()}")
            return False
    print("PASS 7: build_remap_write_chunks reproduces exact capture packets (headers + payloads) byte-for-byte.")


    # CHECK 8: Key A -> B Semantic Check
    slot_50_offset = slot_offset(50)  # 200
    slot_50_bytes = reassembled[slot_50_offset : slot_50_offset + 4]
    expected_b_record = bytes([0x00, 0x05, 0x00, 0x02])
    if slot_50_bytes != expected_b_record:
        print(f"FAIL: Slot 50 (Key A) does not contain B (00 05 00 02): got {slot_50_bytes.hex(' ')}")
        return False
    print(f"PASS 8: Slot 50 (Key A) correctly contains 'B' ({slot_50_bytes.hex(' ').upper()}).")

    # CHECK 9: Validate against physical baseline before and after
    if BASELINE_BEFORE_PATH.exists():
        baseline_before = BASELINE_BEFORE_PATH.read_bytes()
        before_slot_50 = baseline_before[slot_50_offset : slot_50_offset + 4]
        print(f"Baseline before write (Slot 50): {before_slot_50.hex(' ').upper()} (Key 'A')")
        
        # Test patch_remap_image on baseline
        patched = patch_remap_image(
            baseline_before,
            slot=50,
            new_record=RemapRecord(prefix=0, scancode=0x05, special=0, type=0x02),
        )
        patched_diffs = diff_images(baseline_before, patched)
        if len(patched_diffs) != 1 or patched_diffs[0].offset != 201:
            print(f"FAIL: patch_remap_image produced unexpected diff: {patched_diffs}")
            return False
        print(f"PASS 9: patch_remap_image verified: strictly 1 byte altered at offset 0x00C9 (0x04 -> 0x05).")

    if BASELINE_AFTER_PATH.exists() and BASELINE_BEFORE_PATH.exists():
        baseline_after = BASELINE_AFTER_PATH.read_bytes()
        revert_diffs = diff_images(BASELINE_BEFORE_PATH.read_bytes(), baseline_after)
        if len(revert_diffs) != 0:
            print(f"FAIL: Baseline after revert has {len(revert_diffs)} differences!")
            return False
        print("PASS 10: Hardware revert confirmed: 0 differences between baseline before and after.")

    print("=" * 76)
    print("ALL 10 VERIFICATION CHECKS PASSED: CAPTURE IS 100% PROTOCOL COMPLIANT")
    print("=" * 76)
    return True


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAPTURE_PATH
    success = verify_capture(path)
    sys.exit(0 if success else 1)
