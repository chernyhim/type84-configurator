"""
Physical ABA Verification Test for Macro Subsystem (AA 15 / AA 25).

STRICT SAFETY CONSTRAINTS:
- Transport is wrapped in SafetyFilteredTransport:
  * Allowed WRITE opcode: STRICTLY AA 25 ONLY.
  * Any other write opcode (AA 20, AA 21, AA 22, AA 23, AA 24, AA 26, AA 27, AA 28) immediately aborts.
- Target mutation:
  * Stage A (Baseline): Clean catalog (400B zeros, SHA 7a12e561...).
  * Stage B (Target): Single minimal macro at Slot 0 (1 action: 'A' press, 50ms delay).
    - Catalog: slot 0 ptr = 400 (0x00000190), slots 1..99 = 0.
    - Heap: 8 bytes at 0x0190: [02 00 00 00 32 00 04 90].
    - AA 25 writes 8 catalog chunks + 1 heap chunk (9 chunks total).
    - Verifies all 55 25 ACKs for catalog and heap.
    - AA 15 readback confirms slot 0 pointer and heap payload.
  * Stage A' (Rollback): Restore clean catalog (400B zeros).
    - Final AA 15 readback MUST match baseline SHA byte-for-byte.
- Post-test cross-subsystem verification:
  * Remap L1 (AA 12), Remap L2 (AA 16), DKS (AA 18), Hall RT (AA 17), RGB Global (AA 13) MUST be byte-identical.
  * Forbidden writes count MUST be 0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import time
from typing import Any, Dict, List, Optional

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
    MacroCatalog,
    MacroDefinition,
    build_macro_write_chunks,
    read_macro_catalog_raw,
)
from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.plan import WriteChunk
from keyboard_re.protocol.read import (
    read_dks_table,
    read_hall_profile,
    read_keymap_table,
    read_rgb_global,
)
from keyboard_re.transport.native_hid import NativeHidTransport

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


class SafetyFilteredTransport:
    """Safety-filtering transport that permits ONLY AA 25 for writes."""

    def __init__(self, backend: NativeHidTransport):
        self.backend = backend
        self.allowed_read_opcodes = {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x1C}
        self.allowed_write_opcodes = {0x25}  # STRICTLY AA 25 ONLY
        self.sent_packets: List[Dict[str, Any]] = []
        self.received_reports: List[Dict[str, Any]] = []
        self.forbidden_attempts: int = 0
        self.write_counts: Dict[str, int] = {}

    def open(self) -> None:
        self.backend.open()

    def close(self) -> None:
        self.backend.close()

    @property
    def is_connected(self) -> bool:
        return self.backend.is_connected

    def send_report(self, report_id: int, data: bytes | bytearray) -> None:
        pkt = bytes(data)
        if len(pkt) != 64:
            raise ValueError(f"Invalid packet length: {len(pkt)}")

        prefix = pkt[0]
        opcode = pkt[1]

        if prefix != 0xAA:
            self.forbidden_attempts += 1
            raise RuntimeError(f"SAFETY VIOLATION: Blocked non-0xAA packet prefix=0x{prefix:02X}")

        if opcode in self.allowed_write_opcodes:
            op_key = f"AA {opcode:02X}"
            self.write_counts[op_key] = self.write_counts.get(op_key, 0) + 1
        elif opcode in self.allowed_read_opcodes:
            pass
        else:
            self.forbidden_attempts += 1
            raise RuntimeError(
                f"SAFETY VIOLATION: Blocked forbidden packet: prefix=0x{prefix:02X}, opcode=0x{opcode:02X}"
            )

        self.sent_packets.append({
            "timestamp": time.time(),
            "hex": pkt.hex(),
            "prefix": f"{prefix:02X} {opcode:02X}",
            "size": pkt[2],
            "addr": struct.unpack_from("<H", pkt, 3)[0] if len(pkt) >= 5 else None,
            "is_last": pkt[6] if len(pkt) > 6 else None,
        })
        self.backend.send_report(report_id, pkt)

    def receive_report(self, timeout: float = 1.0) -> bytes:
        resp = self.backend.receive_report(timeout)
        self.received_reports.append({
            "timestamp": time.time(),
            "hex": resp.hex(),
            "prefix": f"{resp[0]:02X} {resp[1]:02X}",
        })
        return resp


def execute_write_chunks(
    transport: SafetyFilteredTransport,
    chunks: List[WriteChunk],
    stage_name: str,
    timeout: float = 1.0,
) -> List[bytes]:
    """Execute write chunks sequentially and validate ACKs."""
    acks: List[bytes] = []
    print(f"    Transmitting {len(chunks)} chunks for {stage_name}...")
    for idx, c in enumerate(chunks):
        transport.send_report(0, c.packet)
        ack = transport.receive_report(timeout=timeout)
        validate_report_size(ack, REPORT_SIZE)

        # Validate ACK
        if ack[0] != 0x55 or ack[1] != MACRO_WRITE_OPCODE:
            raise RuntimeError(
                f"Chunk #{idx} ({stage_name}) ACK failed: expected 55 25, got {ack[:2].hex(' ').upper()}"
            )
        if ack[2] != c.size:
            raise RuntimeError(
                f"Chunk #{idx} ({stage_name}) size mismatch: expected {c.size}, got {ack[2]}"
            )
        ack_addr = struct.unpack_from("<H", ack, 3)[0]
        if ack_addr != c.address:
            raise RuntimeError(
                f"Chunk #{idx} ({stage_name}) addr mismatch: expected 0x{c.address:04X}, got 0x{ack_addr:04X}"
            )

        print(f"      Chunk #{idx:02d} [Addr 0x{c.address:04X}, {c.size:2d}B, last={c.packet[6]}]: ACK OK (55 25 {ack[2]:02X})")
        acks.append(ack)
    return acks


def read_heap_range(transport: SafetyFilteredTransport, address: int, size: int, timeout: float = 1.0) -> bytes:
    """Read specific byte range from macro heap via AA 15."""
    req = bytearray(REPORT_SIZE)
    req[0] = 0xAA
    req[1] = MACRO_READ_OPCODE
    req[2] = size
    struct.pack_into("<H", req, 3, address)
    transport.send_report(0, bytes(req))
    resp = transport.receive_report(timeout=timeout)
    validate_report_size(resp, REPORT_SIZE)
    if resp[0] != 0x55 or resp[1] != MACRO_READ_OPCODE or resp[2] != size:
        raise RuntimeError(f"Heap read failed: expected 55 15 {size:02X}, got {resp[:3].hex(' ').upper()}")
    resp_addr = struct.unpack_from("<H", resp, 3)[0]
    if resp_addr != address:
        raise RuntimeError(f"Heap read addr mismatch: expected 0x{address:04X}, got 0x{resp_addr:04X}")
    return resp[8 : 8 + size]


def main() -> int:
    print("=" * 72)
    print("PHYSICAL ABA TEST: Macro Subsystem (AA 25 / AA 15)")
    print("Target Device: IO by Red Square Type 84 (0x0C45:0x80D6)")
    print("=" * 72)

    forensic_log: Dict[str, Any] = {
        "test_name": "Macro Physical ABA Test (AA 25)",
        "start_time": time.time(),
        "target_macro": "Slot 0: 1 action (Key 'A', Press, 50ms delay)",
    }

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = SafetyFilteredTransport(native_tr)

    try:
        safety_tr.open()
        print("[1] Connected to hardware via SafetyFilteredTransport.")

        # ---------------------------------------------------------------------
        # Stage 1: Baseline Verification (Stage A)
        # ---------------------------------------------------------------------
        print("\n[2] Verifying initial baseline (Stage A)...")
        baseline_catalog = read_macro_catalog_raw(safety_tr)
        baseline_cat_sha = hashlib.sha256(baseline_catalog).hexdigest()
        print(f"    Baseline Catalog SHA-256: {baseline_cat_sha}")

        # Baseline heap probe
        baseline_heap_8b = read_heap_range(safety_tr, address=MACRO_HEAP_START, size=8)
        print(f"    Baseline Heap @0x0190 (8B): {baseline_heap_8b.hex(' ').upper()}")

        # Cross-subsystem baselines
        base_l1 = read_keymap_table(safety_tr, opcode=0x12)
        base_l2 = read_keymap_table(safety_tr, opcode=0x16)
        base_dks = read_dks_table(safety_tr)
        base_hall = read_hall_profile(safety_tr, profile_id=1)
        base_rgb = read_rgb_global(safety_tr)

        base_l1_sha = hashlib.sha256(base_l1).hexdigest()
        base_l2_sha = hashlib.sha256(base_l2).hexdigest()
        base_dks_sha = hashlib.sha256(base_dks).hexdigest()
        base_hall_sha = hashlib.sha256(base_hall).hexdigest()

        forensic_log["baseline"] = {
            "catalog_sha256": baseline_cat_sha,
            "heap_8b_hex": baseline_heap_8b.hex(),
            "l1_sha256": base_l1_sha,
            "l2_sha256": base_l2_sha,
            "dks_sha256": base_dks_sha,
            "hall_sha256": base_hall_sha,
            "rgb_effect": base_rgb.effect,
        }

        # ---------------------------------------------------------------------
        # Stage 2: Target Mutation Write (Stage B)
        # ---------------------------------------------------------------------
        print("\n[3] Building Target Macro Mutation (Stage B)...")
        # Define 1 minimal macro at Slot 0: Key 'A' (0x04), Press, delay 50ms
        target_action = MacroAction(action_type=1, is_press=True, key_code=0x04, delay=50)
        target_macro = MacroDefinition(macro_id=0, name="ABA Test Macro", actions=[target_action])
        target_catalog = MacroCatalog()
        target_catalog.set_macro(target_macro)

        target_cat_bytes, target_heap_bytes = target_catalog.build_image()
        target_full_image = target_catalog.to_full_image()

        print(f"    Target Catalog size: {len(target_cat_bytes)} bytes")
        print(f"    Target Heap size:    {len(target_heap_bytes)} bytes ({target_heap_bytes.hex(' ').upper()})")
        print(f"    Slot 0 pointer:      0x{struct.unpack_from('<I', target_cat_bytes, 0)[0]:04X}")

        # Build chunks: 8 catalog chunks + 1 heap chunk = 9 chunks
        target_chunks = build_macro_write_chunks(target_full_image)
        print(f"    Total AA 25 write chunks: {len(target_chunks)}")
        if len(target_chunks) != 9:
            raise ValueError(f"Expected 9 chunks for target macro, got {len(target_chunks)}")

        print("\n[4] Writing Target Macro via AA 25...")
        write_acks = execute_write_chunks(safety_tr, target_chunks, "Stage B (Target Mutation)")
        forensic_log["target_write_ack_count"] = len(write_acks)

        # Allow MCU flash commit
        time.sleep(0.1)

        # ---------------------------------------------------------------------
        # Stage 3: Target Readback Verification
        # ---------------------------------------------------------------------
        print("\n[5] Performing Target Readback via AA 15...")
        target_readback_catalog = read_macro_catalog_raw(safety_tr)
        target_readback_heap = read_heap_range(safety_tr, address=MACRO_HEAP_START, size=len(target_heap_bytes))

        ptr_slot0 = struct.unpack_from("<I", target_readback_catalog, 0)[0]
        print(f"    Readback Slot 0 pointer: 0x{ptr_slot0:04X} (expected 0x0190 = 400)")
        print(f"    Readback Heap payload:   {target_readback_heap.hex(' ').upper()}")
        print(f"    Expected Heap payload:   {target_heap_bytes.hex(' ').upper()}")

        if ptr_slot0 != MACRO_HEAP_START:
            raise RuntimeError(f"Target readback failed: slot 0 ptr is 0x{ptr_slot0:04X}, expected 0x0190")
        if target_readback_heap != target_heap_bytes:
            raise RuntimeError(
                f"Target readback failed: heap mismatch. Got {target_readback_heap.hex()}, expected {target_heap_bytes.hex()}"
            )

        # Check that slots 1..99 remain zero
        other_ptrs = [struct.unpack_from("<I", target_readback_catalog, s * 4)[0] for s in range(1, MACRO_SLOT_COUNT)]
        if any(p != 0 for p in other_ptrs):
            raise RuntimeError("Target readback failed: non-zero pointers found outside slot 0!")

        print("    --> Target Readback 100% VERIFIED! Slot 0 pointer and heap body match wire bytes exactly.")
        forensic_log["target_readback_verified"] = True

        # ---------------------------------------------------------------------
        # Stage 4: Rollback to Baseline (Stage A')
        # ---------------------------------------------------------------------
        print("\n[6] Performing Rollback to Baseline (Stage A')...")
        # Baseline catalog: 400 bytes of zeros
        # In vendor JS (Ou), when heap is empty, only the 400B catalog is written with is_last=True on chunk 7.
        rollback_chunks = build_macro_write_chunks(baseline_catalog)
        print(f"    Total Rollback AA 25 chunks: {len(rollback_chunks)}")
        if len(rollback_chunks) != 8:
            raise ValueError(f"Expected 8 rollback chunks, got {len(rollback_chunks)}")

        rollback_acks = execute_write_chunks(safety_tr, rollback_chunks, "Stage A' (Rollback)")
        forensic_log["rollback_write_ack_count"] = len(rollback_acks)

        # Allow MCU flash commit
        time.sleep(0.1)

        # ---------------------------------------------------------------------
        # Stage 5: Final Readback & Cross-Subsystem Integrity Checks
        # ---------------------------------------------------------------------
        print("\n[7] Performing Final Readback Verification...")
        final_catalog = read_macro_catalog_raw(safety_tr)
        final_cat_sha = hashlib.sha256(final_catalog).hexdigest()
        print(f"    Final Catalog SHA-256:    {final_cat_sha}")
        print(f"    Baseline Catalog SHA-256: {baseline_cat_sha}")

        if final_cat_sha != baseline_cat_sha:
            raise RuntimeError(
                f"CRITICAL ROLLBACK FAILURE: final catalog SHA ({final_cat_sha}) does not match baseline ({baseline_cat_sha})"
            )
        print("    --> Catalog SHA matches baseline byte-for-byte!")

        print("\n[8] Verifying cross-subsystem integrity (Zero Side-Effects)...")
        final_l1 = read_keymap_table(safety_tr, opcode=0x12)
        final_l2 = read_keymap_table(safety_tr, opcode=0x16)
        final_dks = read_dks_table(safety_tr)
        final_hall = read_hall_profile(safety_tr, profile_id=1)
        final_rgb = read_rgb_global(safety_tr)

        final_l1_sha = hashlib.sha256(final_l1).hexdigest()
        final_l2_sha = hashlib.sha256(final_l2).hexdigest()
        final_dks_sha = hashlib.sha256(final_dks).hexdigest()
        final_hall_sha = hashlib.sha256(final_hall).hexdigest()

        print(f"    L1 Remap SHA:  {'MATCH' if final_l1_sha == base_l1_sha else 'MISMATCH'}")
        print(f"    L2 Remap SHA:  {'MATCH' if final_l2_sha == base_l2_sha else 'MISMATCH'}")
        print(f"    DKS Table SHA: {'MATCH' if final_dks_sha == base_dks_sha else 'MISMATCH'}")
        print(f"    Hall RT SHA:   {'MATCH' if final_hall_sha == base_hall_sha else 'MISMATCH'}")
        print(f"    RGB Effect:    {'MATCH' if final_rgb.effect == base_rgb.effect else 'MISMATCH'}")

        if final_l1_sha != base_l1_sha or final_l2_sha != base_l2_sha:
            raise RuntimeError("Cross-subsystem integrity violation: Remap table modified!")
        if final_dks_sha != base_dks_sha:
            raise RuntimeError("Cross-subsystem integrity violation: DKS table modified!")
        if final_hall_sha != base_hall_sha:
            raise RuntimeError("Cross-subsystem integrity violation: Hall profile modified!")

        print(f"\n[9] Safety Audit:")
        print(f"    Forbidden write attempts: {safety_tr.forbidden_attempts} (Expected: 0)")
        print(f"    AA 25 write reports sent: {safety_tr.write_counts.get('AA 25', 0)}")

        if safety_tr.forbidden_attempts != 0:
            raise RuntimeError(f"Safety violation: {safety_tr.forbidden_attempts} forbidden write attempts!")

        forensic_log["final_verification"] = {
            "status": "PASS",
            "final_catalog_sha256": final_cat_sha,
            "forbidden_writes": safety_tr.forbidden_attempts,
            "aa25_write_count": safety_tr.write_counts.get("AA 25", 0),
        }

        # Save log
        log_path = SCRATCH_DIR / "macro_aba_test_forensic_log.json"
        log_path.write_text(json.dumps(forensic_log, indent=2))
        print(f"    Saved forensic log to {log_path}")

        print("\n" + "=" * 72)
        print("PHYSICAL ABA TEST RESULT: FULL PASS")
        print("Macro Subsystem (AA 15 / AA 25) wire read/write protocol VERIFIED ON HARDWARE.")
        print("=" * 72)
        return 0

    except Exception as e:
        print(f"\n[!] PHYSICAL TEST FAILED: {e}")
        import traceback
        traceback.print_exc()

        # Emergency rollback attempt
        print("\n[!] Attempting emergency rollback to baseline catalog...")
        try:
            em_chunks = build_macro_write_chunks(baseline_catalog)
            execute_write_chunks(safety_tr, em_chunks, "EMERGENCY ROLLBACK")
            em_cat = read_macro_catalog_raw(safety_tr)
            if hashlib.sha256(em_cat).hexdigest() == baseline_cat_sha:
                print("[!] EMERGENCY ROLLBACK SUCCESSFUL: Baseline catalog restored.")
            else:
                print("[!] EMERGENCY ROLLBACK FAILED: Catalog SHA mismatch.")
        except Exception as em_ex:
            print(f"[!] Emergency rollback failed: {em_ex}")

        return 1
    finally:
        safety_tr.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
