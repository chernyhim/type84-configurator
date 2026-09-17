"""
Physical Hardware ABA Validation Script for RGB Per-Key (Opcode AA 24 / 55 14).
Tests closed-loop physical writes against physical IO Type 84 keyboard.

Safety policy:
- Transport is wrapped with PerKeySafetyTransport.
- ONLY opcode 0x24 (AA 24) is permitted for write operations.
- All other write opcodes are strictly forbidden and will abort immediately.
- Reads (AA 10, 11, 12, 13, 14, 15, 16, 17, 18, 1C) are strictly read-only.
- Input buffers are drained prior to writes and reads.

Sequence:
1. Baseline readback AA 14 + collect all subsystem baselines.
2. Mutate exactly 1 LED color byte in slot 35 ('W').
3. AA 24 write (10 chunks) with ACK validation (55 24).
4. AA 14 readback: verify exact target state match (target readback exact).
5. Restore baseline AA 24 write (10 chunks) with ACK validation.
6. AA 14 final readback: verify final SHA == baseline SHA.
7. Verify all other subsystems unchanged.
8. Verify forbidden writes == 0.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from typing import Dict, Optional, Tuple

from keyboard_re.models.state import DeviceState, collect_device_state
from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.read import read_rgb_per_key
from keyboard_re.protocol.rgb import (
    LED_BUFFER_SIZE,
    LED_SLOT_COUNT,
    LED_SLOT_SIZE,
    build_rgb_per_key_chunks,
)
from keyboard_re.transport.native_hid import NativeHidTransport


class PerKeySafetyTransport:
    """Safety wrapper that permits ONLY AA 24 writes and tracks any violations."""

    def __init__(self, inner: NativeHidTransport):
        self.inner = inner
        self.forbidden_writes = 0
        self.allowed_writes = 0
        self.write_log = []

    def open(self):
        self.inner.open()

    def close(self):
        self.inner.close()

    @property
    def is_connected(self) -> bool:
        return self.inner.is_connected

    def drain_input_buffer(self, max_reports: int = 64) -> int:
        return self.inner.drain_input_buffer(max_reports=max_reports)

    def send_report(self, report_id: int, data: bytes) -> None:
        if len(data) < 2:
            raise ValueError("Packet too short")

        prefix = data[0]
        opcode = data[1]

        # Check if write operation
        if prefix == 0xAA:
            # Opcode 0x24 is the ONLY allowed write opcode
            if opcode in (0x21, 0x22, 0x23, 0x25, 0x26, 0x27, 0x28):
                self.forbidden_writes += 1
                raise PermissionError(
                    f"FORBIDDEN WRITE ATTEMPT: Opcode 0x{opcode:02X} is not allowed! Only 0x24 is permitted."
                )
            elif opcode == 0x24:
                self.allowed_writes += 1
                self.write_log.append(f"AA 24 sz={data[2]} addr={struct.unpack_from('<H', data, 3)[0]}")
            elif opcode in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x1C):
                pass  # Read command
            else:
                self.forbidden_writes += 1
                raise PermissionError(
                    f"UNKNOWN/FORBIDDEN OPCODE: 0x{opcode:02X}"
                )

        self.inner.send_report(report_id, data)

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: Optional[int] = None,
        expected_address: Optional[int] = None,
    ) -> bytes:
        return self.inner.receive_report(
            timeout=timeout,
            expected_opcode=expected_opcode,
            expected_address=expected_address,
        )


def run_aba_test():
    raw_transport = NativeHidTransport()
    raw_transport.open()
    transport = PerKeySafetyTransport(raw_transport)

    forensic_log = {
        "timestamp": time.time(),
        "steps": [],
        "success": False,
    }

    try:
        # -------------------------------------------------------------
        # 0. Drain initial stale packets
        # -------------------------------------------------------------
        drained = transport.drain_input_buffer()
        print(f"[PREFLIGHT] Initial drained reports: {drained}")

        # -------------------------------------------------------------
        # 1. Capture baseline state across all subsystems
        # -------------------------------------------------------------
        print("\n[STEP 1] Reading initial full keyboard state (subsystems baseline)...")
        initial_state = collect_device_state(transport, timeout=1.5)

        baseline_subsystems = {
            "game_mode": initial_state.game_mode.raw_payload.hex() if initial_state.game_mode else None,
            "remap_l1": hashlib.sha256(initial_state.remap_l1.raw_bytes).hexdigest() if initial_state.remap_l1 else None,
            "remap_l2": hashlib.sha256(initial_state.remap_l2.raw_bytes).hexdigest() if initial_state.remap_l2 else None,
            "remap_l3": hashlib.sha256(initial_state.remap_l3_raw).hexdigest() if initial_state.remap_l3_raw else None,
            "rgb_global": initial_state.rgb_global.raw_payload.hex() if initial_state.rgb_global else None,
            "macro": hashlib.sha256(initial_state.macro_table_raw).hexdigest() if initial_state.macro_table_raw else None,
            "dks": hashlib.sha256(initial_state.dks_table_raw).hexdigest() if initial_state.dks_table_raw else None,
            "hall_p1": hashlib.sha256(initial_state.profiles[1].hall.to_bytes()).hexdigest() if initial_state.profiles.get(1) and initial_state.profiles[1].hall else None,
        }
        for sub, val in baseline_subsystems.items():
            print(f"  * Baseline {sub}: {val}")

        # Read dedicated Per-Key RGB baseline via AA 14
        print("\n[STEP 1b] Reading dedicated Per-Key RGB baseline via AA 14...")
        transport.drain_input_buffer()
        baseline_rgb_matrix = read_rgb_per_key(transport, timeout=1.5)
        assert len(baseline_rgb_matrix) == LED_BUFFER_SIZE, f"Expected 512B, got {len(baseline_rgb_matrix)}"
        baseline_sha = hashlib.sha256(baseline_rgb_matrix).hexdigest()
        print(f"  * Baseline Per-Key RGB SHA-256: {baseline_sha}")

        target_slot = 35  # Key 'W'
        orig_slot_bytes = list(baseline_rgb_matrix[target_slot * 4 : (target_slot + 1) * 4])
        print(f"  * Target Slot {target_slot} (Key 'W') initial bytes: {orig_slot_bytes}")

        forensic_log["baseline_sha"] = baseline_sha
        forensic_log["orig_slot_bytes"] = orig_slot_bytes
        forensic_log["baseline_subsystems"] = baseline_subsystems

        # -------------------------------------------------------------
        # 2. Mutate exactly 1 LED color byte (State B)
        # -------------------------------------------------------------
        print("\n[STEP 2] Mutating LED slot 35 color (State B)...")
        target_rgb_matrix = bytearray(baseline_rgb_matrix)
        
        # Toggle green byte (byte 2 of slot): if 0 -> 255, if 255 -> 0
        new_green = 255 if orig_slot_bytes[2] == 0 else 0
        target_rgb_matrix[target_slot * 4 + 2] = new_green
        target_slot_bytes = list(target_rgb_matrix[target_slot * 4 : (target_slot + 1) * 4])
        print(f"  * Target Slot {target_slot} modified bytes: {target_slot_bytes}")

        target_sha = hashlib.sha256(target_rgb_matrix).hexdigest()
        print(f"  * Target Per-Key RGB SHA-256: {target_sha}")
        assert target_sha != baseline_sha, "Target SHA must differ from baseline!"

        # Build 10 write chunks with canonical offset 8
        target_chunks = build_rgb_per_key_chunks(target_rgb_matrix)
        assert len(target_chunks) == 10, f"Expected 10 chunks, got {len(target_chunks)}"

        # -------------------------------------------------------------
        # 3. Transmit AA 24 write sequence (10 reports)
        # -------------------------------------------------------------
        print("\n[STEP 3] Transmitting AA 24 write packets (State B)...")
        transport.drain_input_buffer()
        for idx, chunk_pkt in enumerate(target_chunks):
            addr = struct.unpack_from("<H", chunk_pkt, 3)[0]
            sz = chunk_pkt[2]
            transport.send_report(0, chunk_pkt)
            
            # Wait for ACK 55 24
            ack = transport.receive_report(timeout=1.5, expected_opcode=0x24, expected_address=addr)
            assert ack[0] == 0x55, f"Invalid ACK prefix: 0x{ack[0]:02X}"
            assert ack[1] == 0x24, f"Invalid ACK opcode: 0x{ack[1]:02X}"
            assert ack[2] == sz, f"Invalid ACK size: {ack[2]} != {sz}"
            ack_addr = struct.unpack_from("<H", ack, 3)[0]
            assert ack_addr == addr, f"Invalid ACK address: 0x{ack_addr:04X} != 0x{addr:04X}"
            print(f"  -> Chunk #{idx} (addr=0x{addr:04X}, sz={sz}) verified ACK 55 24")

        # -------------------------------------------------------------
        # 4. AA 14 Readback State B
        # -------------------------------------------------------------
        print("\n[STEP 4] Reading back Per-Key RGB (State B) via AA 14...")
        transport.drain_input_buffer()
        time.sleep(0.05)
        readback_b = read_rgb_per_key(transport, timeout=1.5)
        readback_b_sha = hashlib.sha256(readback_b).hexdigest()
        readback_b_slot = list(readback_b[target_slot * 4 : (target_slot + 1) * 4])

        print(f"  * Readback B SHA-256: {readback_b_sha}")
        print(f"  * Target Slot {target_slot} readback bytes: {readback_b_slot}")

        # Byte-by-byte diff check
        diffs = []
        for i in range(LED_BUFFER_SIZE):
            if readback_b[i] != target_rgb_matrix[i]:
                diffs.append((i, i // 4, i % 4, target_rgb_matrix[i], readback_b[i]))

        if diffs:
            print(f"  [ERROR] Readback B differs in {len(diffs)} bytes: {diffs[:10]}")
        else:
            print("  [SUCCESS] Target readback is 100% BYTE-EXACT to target state!")

        assert readback_b_sha == target_sha, (
            f"Readback B SHA mismatch: expected {target_sha}, got {readback_b_sha}"
        )
        assert readback_b_slot == target_slot_bytes, (
            f"Slot {target_slot} mismatch: expected {target_slot_bytes}, got {readback_b_slot}"
        )

        # -------------------------------------------------------------
        # 5. Restore Baseline AA 24 write (State A)
        # -------------------------------------------------------------
        print("\n[STEP 5] Restoring baseline state via AA 24...")
        restore_chunks = build_rgb_per_key_chunks(baseline_rgb_matrix)
        assert len(restore_chunks) == 10

        transport.drain_input_buffer()
        for idx, chunk_pkt in enumerate(restore_chunks):
            addr = struct.unpack_from("<H", chunk_pkt, 3)[0]
            sz = chunk_pkt[2]
            transport.send_report(0, chunk_pkt)
            
            ack = transport.receive_report(timeout=1.5, expected_opcode=0x24, expected_address=addr)
            assert ack[0] == 0x55, f"Invalid ACK prefix: 0x{ack[0]:02X}"
            assert ack[1] == 0x24, f"Invalid ACK opcode: 0x{ack[1]:02X}"
            assert ack[2] == sz, f"Invalid ACK size: {ack[2]} != {sz}"
            ack_addr = struct.unpack_from("<H", ack, 3)[0]
            assert ack_addr == addr, f"Invalid ACK address: 0x{ack_addr:04X} != 0x{addr:04X}"
            print(f"  -> Restore Chunk #{idx} (addr=0x{addr:04X}, sz={sz}) verified ACK 55 24")

        # -------------------------------------------------------------
        # 6. AA 14 Final Readback State A
        # -------------------------------------------------------------
        print("\n[STEP 6] Final AA 14 readback to verify baseline restored...")
        transport.drain_input_buffer()
        time.sleep(0.05)
        final_readback = read_rgb_per_key(transport, timeout=1.5)
        final_readback_sha = hashlib.sha256(final_readback).hexdigest()
        final_slot_bytes = list(final_readback[target_slot * 4 : (target_slot + 1) * 4])

        print(f"  * Final Readback SHA-256: {final_readback_sha}")
        print(f"  * Target Slot {target_slot} final bytes: {final_slot_bytes}")

        assert final_readback_sha == baseline_sha, (
            f"Final SHA mismatch: expected {baseline_sha}, got {final_readback_sha}"
        )
        assert final_slot_bytes == orig_slot_bytes, (
            f"Slot {target_slot} not restored: expected {orig_slot_bytes}, got {final_slot_bytes}"
        )
        print("  [SUCCESS] Final SHA-256 exactly matches Baseline SHA-256!")

        # -------------------------------------------------------------
        # 7. Verify all other subsystems unchanged
        # -------------------------------------------------------------
        print("\n[STEP 7] Verifying all other subsystems are completely unchanged...")
        transport.drain_input_buffer()
        final_state = collect_device_state(transport, timeout=1.5)

        final_subsystems = {
            "game_mode": final_state.game_mode.raw_payload.hex() if final_state.game_mode else None,
            "remap_l1": hashlib.sha256(final_state.remap_l1.raw_bytes).hexdigest() if final_state.remap_l1 else None,
            "remap_l2": hashlib.sha256(final_state.remap_l2.raw_bytes).hexdigest() if final_state.remap_l2 else None,
            "remap_l3": hashlib.sha256(final_state.remap_l3_raw).hexdigest() if final_state.remap_l3_raw else None,
            "rgb_global": final_state.rgb_global.raw_payload.hex() if final_state.rgb_global else None,
            "macro": hashlib.sha256(final_state.macro_table_raw).hexdigest() if final_state.macro_table_raw else None,
            "dks": hashlib.sha256(final_state.dks_table_raw).hexdigest() if final_state.dks_table_raw else None,
            "hall_p1": hashlib.sha256(final_state.profiles[1].hall.to_bytes()).hexdigest() if final_state.profiles.get(1) and final_state.profiles[1].hall else None,
        }

        subsystem_mismatches = []
        for sub, orig_val in baseline_subsystems.items():
            fin_val = final_subsystems.get(sub)
            if orig_val != fin_val:
                subsystem_mismatches.append((sub, orig_val, fin_val))
                print(f"  [MISMATCH] Subsystem {sub}: orig={orig_val}, fin={fin_val}")
            else:
                print(f"  [OK] Subsystem {sub} UNCHANGED ({fin_val})")

        assert len(subsystem_mismatches) == 0, f"Cross-subsystem contamination detected: {subsystem_mismatches}"

        # -------------------------------------------------------------
        # 8. Check forbidden writes == 0
        # -------------------------------------------------------------
        print("\n[STEP 8] Verifying write safety metrics...")
        print(f"  * Allowed AA 24 writes: {transport.allowed_writes} packets")
        print(f"  * Forbidden writes attempted: {transport.forbidden_writes}")
        assert transport.forbidden_writes == 0, f"Forbidden writes detected: {transport.forbidden_writes}"
        assert transport.allowed_writes == 20, f"Expected exactly 20 packets (10 write + 10 restore), got {transport.allowed_writes}"

        print("\n" + "=" * 60)
        print("ALL PHYSICAL RGB PER-KEY ABA CHECKS PASSED WITH 100% PRECISION!")
        print("=" * 60)

        forensic_log["success"] = True
        forensic_log["allowed_writes"] = transport.allowed_writes
        forensic_log["forbidden_writes"] = transport.forbidden_writes
        forensic_log["final_sha"] = final_readback_sha

        return True

    finally:
        transport.close()


if __name__ == "__main__":
    success = run_aba_test()
    sys.exit(0 if success else 1)
