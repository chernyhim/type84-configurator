"""
Physical Preflight for Remap Layer 2 / Fn (AA 16).
Target Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)

STRICTLY READ-ONLY:
- Reads: AA 16 (L2), AA 1C (Default Fn / Layer 3), AA 12 (L1).
- Zero writes permitted (SafetyFilteredTransport will raise on any write attempt).
- Computes SHA256 hashes.
- Verifies AA 16 == AA 1C baseline equivalence.
- Saves baseline binaries to scratch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.protocol.read import read_keymap_table
from keyboard_re.transport.native_hid import NativeHidTransport

SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


class SafetyFilteredReadOnlyTransport:
    """Wrapper that intercepts every outgoing packet and ensures STRICTLY READ-ONLY opcodes."""

    def __init__(self, backend: NativeHidTransport):
        self.backend = backend
        self.sent_packets: List[Dict[str, Any]] = []
        self.received_reports: List[Dict[str, Any]] = []
        self.opcode_counts: Dict[str, int] = {
            "AA 16": 0,
            "AA 1C": 0,
            "AA 12": 0,
        }

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

        # STRICT SAFETY GATE: Only read opcodes 0x16, 0x1C, 0x12
        if prefix != 0xAA or opcode not in (0x16, 0x1C, 0x12):
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
    print("PHYSICAL PREFLIGHT: Remap Layer 2 (AA 16) / Default Fn (AA 1C) / L1 (AA 12)")
    print("=" * 68)

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = SafetyFilteredReadOnlyTransport(native_tr)

    try:
        print("[1/4] Connecting to physical keyboard...")
        safety_tr.open()
        if not safety_tr.is_connected:
            print("ERROR: Failed to connect to physical keyboard.")
            return 1
        print("Connected successfully.")

        print("\n[2/4] Reading Layer 2 (AA 16), Default Fn (AA 1C), Layer 1 (AA 12)...")
        # 1. Read L2
        l2_bytes = read_keymap_table(safety_tr, opcode=0x16, timeout=1.5)
        print(f"  -> L2 (AA 16) read {len(l2_bytes)} bytes.")

        # 2. Read Default Fn (AA 1C)
        dfn_bytes = read_keymap_table(safety_tr, opcode=0x1C, timeout=1.5)
        print(f"  -> Default Fn (AA 1C) read {len(dfn_bytes)} bytes.")

        # 3. Read L1 (AA 12)
        l1_bytes = read_keymap_table(safety_tr, opcode=0x12, timeout=1.5)
        print(f"  -> L1 (AA 12) read {len(l1_bytes)} bytes.")

        # Hashes
        sha_l2 = hashlib.sha256(l2_bytes).hexdigest()
        sha_dfn = hashlib.sha256(dfn_bytes).hexdigest()
        sha_l1 = hashlib.sha256(l1_bytes).hexdigest()

        print("\n[3/4] Baseline Hashes:")
        print(f"  L2     (AA 16): {sha_l2}")
        print(f"  Def Fn (AA 1C): {sha_dfn}")
        print(f"  L1     (AA 12): {sha_l1}")

        # Save to scratch
        (SCRATCH_DIR / "physical_baseline_remap_l2.bin").write_bytes(l2_bytes)
        (SCRATCH_DIR / "physical_baseline_remap_dfn.bin").write_bytes(dfn_bytes)
        (SCRATCH_DIR / "physical_baseline_remap_l1.bin").write_bytes(l1_bytes)
        print(f"\nSaved baselines to {SCRATCH_DIR}")

        print("\n[4/4] Verification of Invariants:")
        equiv_l2_dfn = (l2_bytes == dfn_bytes)
        print(f"  AA 16 == AA 1C : {'PASS (IDENTICAL)' if equiv_l2_dfn else 'FAIL (DIFFERENT)'}")

        if not equiv_l2_dfn:
            diff_indices = [i for i in range(512) if l2_bytes[i] != dfn_bytes[i]]
            print(f"  Differences count: {len(diff_indices)} bytes")
            print(f"  Offsets: {diff_indices[:20]}")
            return 2

        print(f"  Zero write packets sent: {all(k.startswith('AA 1') for k in safety_tr.opcode_counts.keys())}")
        print(f"  Opcode counts: {safety_tr.opcode_counts}")
        print("\nPREFLIGHT PASSED: Ready for controlled Physical ABA Test.")
        return 0

    except Exception as e:
        print(f"\nEXCEPTION DURING PREFLIGHT: {e}")
        import traceback
        traceback.print_exc()
        return 3
    finally:
        safety_tr.close()
        print("Device connection closed.")


if __name__ == "__main__":
    sys.exit(main())
