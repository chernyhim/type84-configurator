"""
Physical Read-Only Preflight for Macro Subsystem (AA 15).

STRICT READ-ONLY:
- Zero writes permitted (SafetyFilteredTransport blocks prefix != 0xAA or any write opcode).
- Reads 400B Macro catalog via AA 15 and checks for existing user macros.
- Tests reading small heap range at address 0x0190 (400) via AA 15.
- Records cross-subsystem baseline SHAs (Remap L1, L2, DKS, RGB, Hall).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import time
from typing import Any, Dict, List

from keyboard_re.protocol.macro import (
    MACRO_BUFFER_SIZE,
    MACRO_CHUNK_SIZE,
    MACRO_HEAP_START,
    MACRO_READ_OPCODE,
    MACRO_SLOT_COUNT,
    MACRO_TAIL_ADDR,
    MACRO_TAIL_SIZE,
    read_macro_catalog_raw,
)
from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
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


class ReadOnlySafetyTransport:
    """Strictly read-only safety transport for preflight."""

    def __init__(self, backend: NativeHidTransport):
        self.backend = backend
        self.allowed_read_opcodes = {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x1C}
        self.sent_packets: List[Dict[str, Any]] = []

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

        # STRICT SAFETY: Reject any packet that is not a read opcode
        if prefix != 0xAA or opcode not in self.allowed_read_opcodes:
            raise RuntimeError(
                f"SAFETY VIOLATION: Preflight blocked forbidden outgoing packet: "
                f"prefix=0x{prefix:02X}, opcode=0x{opcode:02X}"
            )

        self.sent_packets.append({
            "timestamp": time.time(),
            "hex": pkt.hex(),
            "opcode": f"{prefix:02X} {opcode:02X}",
            "size": pkt[2],
            "addr": struct.unpack_from("<H", pkt, 3)[0] if len(pkt) >= 5 else None,
        })
        self.backend.send_report(report_id, pkt)

    def receive_report(self, timeout: float = 1.0) -> bytes:
        return self.backend.receive_report(timeout)


def main() -> int:
    print("=" * 72)
    print("PHYSICAL READ-ONLY PREFLIGHT: Macro Subsystem (AA 15)")
    print("Target Device: IO by Red Square Type 84 (0x0C45:0x80D6)")
    print("=" * 72)

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = ReadOnlySafetyTransport(native_tr)

    try:
        safety_tr.open()
        print("[1] Connected to keyboard via ReadOnlySafetyTransport.")

        # ---------------------------------------------------------------------
        # 1. Read 400-byte Macro Catalog via AA 15
        # ---------------------------------------------------------------------
        print("\n[2] Reading 400B Macro Catalog via AA 15...")
        catalog_bytes = read_macro_catalog_raw(safety_tr, timeout=1.0)
        cat_sha = hashlib.sha256(catalog_bytes).hexdigest()
        print(f"    Catalog size: {len(catalog_bytes)} bytes")
        print(f"    Catalog SHA-256: {cat_sha}")

        # Save baseline catalog
        cat_file = SCRATCH_DIR / "baseline_macro_catalog.bin"
        cat_file.write_bytes(catalog_bytes)
        print(f"    Saved catalog baseline to {cat_file}")

        # Inspect slots
        active_slots: Dict[int, int] = {}
        for s in range(MACRO_SLOT_COUNT):
            ptr = struct.unpack_from("<I", catalog_bytes, s * 4)[0]
            if ptr != 0:
                active_slots[s] = ptr

        print(f"    Non-zero macro pointers found: {len(active_slots)}")
        if active_slots:
            for s, ptr in sorted(active_slots.items()):
                print(f"      Slot {s:02d}: heap pointer = 0x{ptr:04X} ({ptr})")
        else:
            print("      All 100 slots are 0x00000000 (clean baseline, no macros configured).")

        # ---------------------------------------------------------------------
        # 2. Test Reading Small Heap Range at address 0x0190 (400)
        # ---------------------------------------------------------------------
        print("\n[3] Probing Heap Read via AA 15 at address 0x0190 (400)...")

        # Probe 4 bytes: [AA 15 04 90 01 00 00 00 ...]
        req_4b = bytearray(REPORT_SIZE)
        req_4b[0] = 0xAA
        req_4b[1] = MACRO_READ_OPCODE
        req_4b[2] = 0x04
        struct.pack_into("<H", req_4b, 3, MACRO_HEAP_START)
        safety_tr.send_report(0, bytes(req_4b))
        resp_4b = safety_tr.receive_report(timeout=1.0)

        validate_report_size(resp_4b, REPORT_SIZE)
        print(f"    Response for 4B at 0x0190: {resp_4b[:16].hex(' ').upper()}")
        print(f"      Prefix: 0x{resp_4b[0]:02X} 0x{resp_4b[1]:02X}")
        print(f"      Size echo: {resp_4b[2]} bytes")
        resp_addr = struct.unpack_from("<H", resp_4b, 3)[0]
        print(f"      Addr echo: 0x{resp_addr:04X} ({resp_addr})")
        print(f"      Payload (4B at offset 8): {resp_4b[8:12].hex(' ').upper()}")

        # Probe 12 bytes: [AA 15 0C 90 01 00 00 00 ...]
        req_12b = bytearray(REPORT_SIZE)
        req_12b[0] = 0xAA
        req_12b[1] = MACRO_READ_OPCODE
        req_12b[2] = 0x0C
        struct.pack_into("<H", req_12b, 3, MACRO_HEAP_START)
        safety_tr.send_report(0, bytes(req_12b))
        resp_12b = safety_tr.receive_report(timeout=1.0)

        validate_report_size(resp_12b, REPORT_SIZE)
        print(f"\n    Response for 12B at 0x0190: {resp_12b[:20].hex(' ').upper()}")
        print(f"      Prefix: 0x{resp_12b[0]:02X} 0x{resp_12b[1]:02X}")
        print(f"      Size echo: {resp_12b[2]} bytes")
        resp_addr_12 = struct.unpack_from("<H", resp_12b, 3)[0]
        print(f"      Addr echo: 0x{resp_addr_12:04X} ({resp_addr_12})")
        print(f"      Payload (12B at offset 8): {resp_12b[8:20].hex(' ').upper()}")

        # ---------------------------------------------------------------------
        # 3. Collect Cross-Subsystem Integrity Baselines
        # ---------------------------------------------------------------------
        print("\n[4] Collecting cross-subsystem baseline checksums...")
        l1_bytes = read_keymap_table(safety_tr, opcode=0x12)
        l2_bytes = read_keymap_table(safety_tr, opcode=0x16)
        dks_bytes = read_dks_table(safety_tr)
        hall_bytes = read_hall_profile(safety_tr, profile_id=1)
        rgb_cfg = read_rgb_global(safety_tr)

        l1_sha = hashlib.sha256(l1_bytes).hexdigest()
        l2_sha = hashlib.sha256(l2_bytes).hexdigest()
        dks_sha = hashlib.sha256(dks_bytes).hexdigest()
        hall_sha = hashlib.sha256(hall_bytes).hexdigest()

        print(f"    Remap L1 SHA:  {l1_sha}")
        print(f"    Remap L2 SHA:  {l2_sha}")
        print(f"    DKS Table SHA: {dks_sha}")
        print(f"    Hall RT SHA:   {hall_sha}")
        print(f"    RGB Effect:    0x{rgb_cfg.effect:02X}")

        # Save checksum file for ABA verification
        checksums = {
            "catalog_sha256": cat_sha,
            "remap_l1_sha256": l1_sha,
            "remap_l2_sha256": l2_sha,
            "dks_sha256": dks_sha,
            "hall_sha256": hall_sha,
            "heap_initial_12b_hex": resp_12b[8:20].hex(),
        }
        import json
        (SCRATCH_DIR / "macro_preflight_checksums.json").write_text(json.dumps(checksums, indent=2))

        print("\n[5] PREFLIGHT SUCCESSFUL.")
        print("    Device is ready for strictly controlled ABA experiment.")
        return 0

    except Exception as e:
        print(f"\n[!] PREFLIGHT FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        safety_tr.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
