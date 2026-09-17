"""
Controlled Physical Write Test: Remap Layer 2 / Fn AA 26 (Baseline -> Target -> Baseline)
Target Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)

SAFETY ENFORCEMENT:
- ONLY allowed write opcode: AA 26 (Remap Layer 2 write)
- Allowed read opcodes: AA 16 (L2 read), AA 1C (Default Fn read), AA 12 (L1 read)
- STRICTLY FORBIDDEN: AA 22, AA 23, AA 24, AA 25, AA 27, AA 28, and any other write opcode.
- Target key: Slot 34 (Key 'W', switch_slot 34, remap_slot 34, offset 136..139).
- Non-protected: Not in F1..F12 (1..12) and not Fn key (85).
- Single change: 02 00 1a 00 -> 02 00 2c 00 (W -> Space), then back to 02 00 1a 00.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import time
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.protocol.executor import validate_chunk_ack
from keyboard_re.protocol.keymap import KeyRemapRecord
from keyboard_re.protocol.plan import build_remap_write_chunks
from keyboard_re.protocol.read import read_keymap_table
from keyboard_re.transport.native_hid import NativeHidTransport

SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


class SafetyFilteredTransport:
    """Wrapper that intercepts every outgoing packet and ensures STRICTLY controlled opcodes."""

    def __init__(self, backend: NativeHidTransport):
        self.backend = backend
        self.sent_packets: List[Dict[str, Any]] = []
        self.received_reports: List[Dict[str, Any]] = []
        self.opcode_counts: Dict[str, int] = {
            "AA 16": 0,
            "AA 1C": 0,
            "AA 12": 0,
            "AA 26": 0,
        }
        self.forbidden_attempts: int = 0

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

        # STRICT SAFETY GATE
        # Allowed writes: ONLY AA 26
        # Allowed reads: AA 16, AA 1C, AA 12
        if prefix != 0xAA or opcode not in (0x16, 0x1C, 0x12, 0x26):
            self.forbidden_attempts += 1
            raise RuntimeError(
                f"SAFETY VIOLATION: Blocked forbidden packet prefix=0x{prefix:02X}, opcode=0x{opcode:02X}"
            )

        op_name = f"{prefix:02X} {opcode:02X}"
        self.opcode_counts[op_name] = self.opcode_counts.get(op_name, 0) + 1

        self.sent_packets.append({
            "timestamp": time.time(),
            "report_id": report_id,
            "hex": pkt.hex(),
            "opcode": op_name,
            "size": pkt[2],
            "addr": struct.unpack_from("<H", pkt, 3)[0] if opcode in (0x16, 0x26, 0x12, 0x1C) else None,
            "is_last": pkt[6],
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


def main() -> int:
    print("=" * 68)
    print("PHYSICAL ABA TEST: Remap Layer 2 / Fn (AA 26)")
    print("Target Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)")
    print("=" * 68)

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = SafetyFilteredTransport(native_tr)

    audit_log: Dict[str, Any] = {
        "timestamp_start": time.time(),
        "device": "IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)",
        "subsystem": "remap_l2",
        "write_opcode": "0x26",
        "read_opcode": "0x16",
        "slot_target": 34,
        "steps": {},
        "verifications": {},
    }

    try:
        safety_tr.open()
        if not safety_tr.is_connected:
            print("ERROR: Failed to connect to physical keyboard.")
            return 1

        # =====================================================================
        # PHASE 1: BASELINE PRE-CHECK
        # =====================================================================
        print("\n--- PHASE 1: BASELINE PRE-CHECK ---")
        l2_baseline = read_keymap_table(safety_tr, opcode=0x16, timeout=1.5)
        dfn_baseline = read_keymap_table(safety_tr, opcode=0x1C, timeout=1.5)
        l1_baseline = read_keymap_table(safety_tr, opcode=0x12, timeout=1.5)

        sha_l2_base = hashlib.sha256(l2_baseline).hexdigest()
        sha_dfn_base = hashlib.sha256(dfn_baseline).hexdigest()
        sha_l1_base = hashlib.sha256(l1_baseline).hexdigest()

        print(f"L2 Baseline SHA256:      {sha_l2_base}")
        print(f"Def Fn Baseline SHA256:  {sha_dfn_base}")
        print(f"L1 Baseline SHA256:      {sha_l1_base}")

        if l2_baseline != dfn_baseline:
            print("ABORT: Initial L2 (AA 16) is not identical to Default Fn (AA 1C)!")
            return 2

        # Check target slot 34 in baseline
        slot34_bytes_base = l2_baseline[136:140]
        print(f"Baseline Slot 34 bytes:  {slot34_bytes_base.hex(' ').upper()} (Key 'W')")
        if slot34_bytes_base != bytes([0x02, 0x00, 0x1A, 0x00]):
            print(f"ABORT: Expected Slot 34 to be 02 00 1A 00, got {slot34_bytes_base.hex(' ')}")
            return 3

        audit_log["baseline"] = {
            "l2_sha": sha_l2_base,
            "dfn_sha": sha_dfn_base,
            "l1_sha": sha_l1_base,
            "slot34": slot34_bytes_base.hex(),
        }

        # =====================================================================
        # PHASE 2: TARGET MUTATION (A -> B)
        # Change Slot 34 from 'W' (0x1A) to 'Space' (0x2C): 02 00 2C 00
        # =====================================================================
        print("\n--- PHASE 2: TARGET MUTATION (W -> Space via AA 26) ---")
        target_l2 = bytearray(l2_baseline)
        target_slot34 = bytes([0x02, 0x00, 0x2C, 0x00])
        target_l2[136:140] = target_slot34
        target_l2_bytes = bytes(target_l2)
        sha_target_l2 = hashlib.sha256(target_l2_bytes).hexdigest()
        print(f"Target L2 SHA256:        {sha_target_l2}")
        print(f"Target Slot 34 bytes:    {target_slot34.hex(' ').upper()} (Key 'Space')")

        # Build chunks using production builder
        chunks_target = build_remap_write_chunks(target_l2_bytes, opcode=0x26)
        print(f"Generated {len(chunks_target)} write chunks with opcode AA 26.")

        acks_target = []
        for i, chunk in enumerate(chunks_target):
            safety_tr.send_report(0, chunk.packet)
            ack = safety_tr.receive_report(timeout=1.0)
            acks_target.append(ack.hex())
            val_err = validate_chunk_ack(chunk, ack, strict_payload=True)
            if val_err:
                raise RuntimeError(f"Chunk #{i} (addr 0x{chunk.address:04X}) ACK validation failed: {val_err}")
            print(f"  Chunk #{i:02d} (addr {chunk.address:03d}, size {chunk.size:02d}B) -> ACK OK (55 26)")

        audit_log["target_write"] = {
            "packets_sent": len(chunks_target),
            "acks_received": len(acks_target),
            "target_sha": sha_target_l2,
        }

        # =====================================================================
        # PHASE 3: READBACK TARGET & CHECK INVARIANTS
        # =====================================================================
        print("\n--- PHASE 3: READBACK TARGET & VERIFICATION ---")
        l2_readback_target = read_keymap_table(safety_tr, opcode=0x16, timeout=1.5)
        sha_readback_target = hashlib.sha256(l2_readback_target).hexdigest()
        print(f"Readback L2 SHA256:      {sha_readback_target}")

        if sha_readback_target != sha_target_l2:
            print(f"ERROR: Readback L2 SHA ({sha_readback_target}) != Target SHA ({sha_target_l2})!")
            return 4
        print("  -> L2 Readback exactly matches target image!")

        # Verify exact diff against baseline
        diff_indices = [i for i in range(512) if l2_readback_target[i] != l2_baseline[i]]
        print(f"  -> Total byte differences against baseline: {len(diff_indices)}")
        print(f"  -> Modified byte offsets: {diff_indices}")
        if diff_indices != [138]:  # offset 138 is scancode byte: 0x1A -> 0x2C
            print(f"ERROR: Unexpected byte diffs: expected [138], got {diff_indices}")
            return 5
        print("  -> EXACTLY ONE BYTE MODIFIED (+138: 0x1A -> 0x2C).")

        # Verify L1 is 100% untouched
        l1_readback_target = read_keymap_table(safety_tr, opcode=0x12, timeout=1.5)
        sha_l1_readback = hashlib.sha256(l1_readback_target).hexdigest()
        print(f"L1 Readback SHA256:      {sha_l1_readback}")
        if sha_l1_readback != sha_l1_base:
            print(f"ERROR: L1 was mutated during L2 write! Base={sha_l1_base}, Read={sha_l1_readback}")
            return 6
        print("  -> L1 IS 100% UNTOUCHED (SHA verified).")

        # Verify Default Fn (AA 1C) is 100% untouched
        dfn_readback_target = read_keymap_table(safety_tr, opcode=0x1C, timeout=1.5)
        sha_dfn_readback = hashlib.sha256(dfn_readback_target).hexdigest()
        print(f"Def Fn Readback SHA256:  {sha_dfn_readback}")
        if sha_dfn_readback != sha_dfn_base:
            print(f"ERROR: Default Fn was mutated! Base={sha_dfn_base}, Read={sha_dfn_readback}")
            return 7
        print("  -> Default Fn IS 100% UNTOUCHED (SHA verified).")

        # =====================================================================
        # PHASE 4: ROLLBACK TO BASELINE (B -> A)
        # Restore Slot 34 to 'W' (0x1A) via AA 26
        # =====================================================================
        print("\n--- PHASE 4: ROLLBACK TO BASELINE (AA 26) ---")
        chunks_rollback = build_remap_write_chunks(l2_baseline, opcode=0x26)
        print(f"Generated {len(chunks_rollback)} rollback chunks with opcode AA 26.")

        acks_rollback = []
        for i, chunk in enumerate(chunks_rollback):
            safety_tr.send_report(0, chunk.packet)
            ack = safety_tr.receive_report(timeout=1.0)
            acks_rollback.append(ack.hex())
            val_err = validate_chunk_ack(chunk, ack, strict_payload=True)
            if val_err:
                raise RuntimeError(f"Rollback chunk #{i} ACK validation failed: {val_err}")
            print(f"  Rollback Chunk #{i:02d} -> ACK OK (55 26)")

        audit_log["rollback_write"] = {
            "packets_sent": len(chunks_rollback),
            "acks_received": len(acks_rollback),
        }

        # =====================================================================
        # PHASE 5: FINAL READBACK & RECOVERY VERIFICATION
        # =====================================================================
        print("\n--- PHASE 5: FINAL VERIFICATION & RECOVERY CHECK ---")
        l2_final = read_keymap_table(safety_tr, opcode=0x16, timeout=1.5)
        dfn_final = read_keymap_table(safety_tr, opcode=0x1C, timeout=1.5)
        l1_final = read_keymap_table(safety_tr, opcode=0x12, timeout=1.5)

        sha_l2_final = hashlib.sha256(l2_final).hexdigest()
        sha_dfn_final = hashlib.sha256(dfn_final).hexdigest()
        sha_l1_final = hashlib.sha256(l1_final).hexdigest()

        print(f"Final L2 SHA256:         {sha_l2_final}")
        print(f"Final Def Fn SHA256:     {sha_dfn_final}")
        print(f"Final L1 SHA256:         {sha_l1_final}")

        v_l2 = (sha_l2_final == sha_l2_base)
        v_dfn = (sha_dfn_final == sha_dfn_base)
        v_l1 = (sha_l1_final == sha_l1_base)
        v_equiv = (l2_final == dfn_final)
        v_forbidden = (safety_tr.forbidden_attempts == 0)

        print(f"\nFinal Audit Invariants:")
        print(f"  1. Final L2 == Baseline L2:        {'PASS' if v_l2 else 'FAIL'}")
        print(f"  2. Final Def Fn == Baseline Def Fn:{'PASS' if v_dfn else 'FAIL'}")
        print(f"  3. Final L1 == Baseline L1:        {'PASS' if v_l1 else 'FAIL'}")
        print(f"  4. Final L2 == Final Def Fn:       {'PASS' if v_equiv else 'FAIL'}")
        print(f"  5. Forbidden Writes Attempted:     {safety_tr.forbidden_attempts} ({'PASS' if v_forbidden else 'FAIL'})")
        print(f"  6. Total AA 26 Packets Sent:       {safety_tr.opcode_counts['AA 26']}")
        print(f"  7. Total Read Packets Sent:        AA 16={safety_tr.opcode_counts['AA 16']}, AA 1C={safety_tr.opcode_counts['AA 1C']}, AA 12={safety_tr.opcode_counts['AA 12']}")

        all_passed = v_l2 and v_dfn and v_l1 and v_equiv and v_forbidden
        print(f"\n{'=' * 68}")
        print(f"PHYSICAL ABA TEST RESULT: {'SUCCESS (100% VERIFIED)' if all_passed else 'FAILURE'}")
        print(f"{'=' * 68}")

        audit_log["final"] = {
            "l2_sha": sha_l2_final,
            "dfn_sha": sha_dfn_final,
            "l1_sha": sha_l1_final,
            "opcode_counts": safety_tr.opcode_counts,
            "forbidden_attempts": safety_tr.forbidden_attempts,
            "success": all_passed,
        }
        audit_log["timestamp_end"] = time.time()

        (SCRATCH_DIR / "remap_l2_aba_test_audit_log.json").write_text(
            json.dumps(audit_log, indent=2), encoding="utf-8"
        )
        return 0 if all_passed else 10

    except Exception as e:
        print(f"\nEXCEPTION DURING ABA TEST: {e}")
        import traceback
        traceback.print_exc()
        return 99
    finally:
        safety_tr.close()
        print("Device connection closed safely.")


if __name__ == "__main__":
    sys.exit(main())
