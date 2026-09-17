"""
Physical Preflight for Game Mode / Settings (AA 11).
Target Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)

STRICTLY READ-ONLY:
- Reads: AA 11 (Game Mode), AA 12 (Remap L1), AA 16 (Remap L2), AA 18 (DKS).
- Zero writes permitted (SafetyFilteredReadOnlyTransport will raise on any write attempt).
- Computes SHA256 hashes.
- Validates current baseline settings and saves physical_baseline_game_mode.bin.
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

from keyboard_re.protocol.read import (
    read_dks_table,
    read_game_mode,
    read_keymap_table,
)
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
            "AA 11": 0,
            "AA 12": 0,
            "AA 16": 0,
            "AA 18": 0,
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

        # STRICT SAFETY GATE: Only read opcodes 0x11, 0x12, 0x16, 0x18
        if prefix != 0xAA or opcode not in (0x11, 0x12, 0x16, 0x18):
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
    print("PHYSICAL PREFLIGHT: Game Mode / Settings (AA 11) - STRICTLY READ-ONLY")
    print("=" * 68)

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = SafetyFilteredReadOnlyTransport(native_tr)

    try:
        safety_tr.open()
        print("[1] Connected to physical hardware via NativeHidTransport.")

        # Read Game Mode (AA 11)
        print("[2] Reading Game Mode / Settings (AA 11)...")
        gm_resp = read_game_mode(safety_tr)
        gm_payload = gm_resp.to_payload()
        gm_sha256 = hashlib.sha256(gm_payload).hexdigest()
        print(f"    -> Game Mode 56B payload read: SHA256={gm_sha256}")
        print(f"       sleep_time={gm_resp.sleep_time} min")
        print(f"       report_rate={gm_resp.report_rate} ({gm_resp.report_rate_hz} Hz)")
        print(f"       stability_mode={gm_resp.stability_mode}")
        print(f"       auto_calibration={gm_resp.auto_calibration}")
        print(f"       game_mode={gm_resp.game_mode}")
        print(f"       fn_switch={gm_resp.fn_switch}")
        print(f"       key_delay={gm_resp.key_delay} ms")
        print(f"       system_mode={gm_resp.system_mode}")
        print(f"       top_deadzone={gm_resp.top_deadzone:.2f} mm")
        print(f"       bottom_deadzone={gm_resp.bottom_deadzone:.2f} mm")

        # Read other subsystems for baseline isolation
        print("[3] Reading Remap L1 (AA 12)...")
        l1_bytes = read_keymap_table(safety_tr, opcode=0x12)
        l1_sha256 = hashlib.sha256(l1_bytes).hexdigest()
        print(f"    -> Remap L1 512B read: SHA256={l1_sha256}")

        print("[4] Reading Remap L2 (AA 16)...")
        l2_bytes = read_keymap_table(safety_tr, opcode=0x16)
        l2_sha256 = hashlib.sha256(l2_bytes).hexdigest()
        print(f"    -> Remap L2 512B read: SHA256={l2_sha256}")

        print("[5] Reading DKS Table (AA 18)...")
        dks_bytes = read_dks_table(safety_tr)
        dks_sha256 = hashlib.sha256(dks_bytes).hexdigest()
        print(f"    -> DKS Table 1024B read: SHA256={dks_sha256}")

        # Save baseline binary
        baseline_file = SCRATCH_DIR / "physical_baseline_game_mode.bin"
        baseline_file.write_bytes(gm_payload)
        print(f"[6] Saved baseline binary to {baseline_file}")

        # Save report
        report = {
            "timestamp": time.time(),
            "target_device": "IO Type 84 (0x0C45:0x80D6)",
            "game_mode_sha256": gm_sha256,
            "remap_l1_sha256": l1_sha256,
            "remap_l2_sha256": l2_sha256,
            "dks_sha256": dks_sha256,
            "game_mode_fields": {
                "sleep_time": gm_resp.sleep_time,
                "report_rate": gm_resp.report_rate,
                "report_rate_hz": gm_resp.report_rate_hz,
                "stability_mode": gm_resp.stability_mode,
                "auto_calibration": gm_resp.auto_calibration,
                "game_mode": gm_resp.game_mode,
                "fn_switch": gm_resp.fn_switch,
                "key_delay": gm_resp.key_delay,
                "system_mode": gm_resp.system_mode,
                "top_deadzone": gm_resp.top_deadzone,
                "bottom_deadzone": gm_resp.bottom_deadzone,
            },
            "opcode_counts": safety_tr.opcode_counts,
        }
        report_file = SCRATCH_DIR / "physical_game_mode_preflight_report.json"
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[7] Preflight report saved to {report_file}")

        print("=" * 68)
        print("PREFLIGHT STATUS: PASS - All baseline hashes recorded, ZERO writes performed.")
        print("=" * 68)
        return 0

    except Exception as ex:
        print(f"[-] Preflight ERROR: {ex}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        safety_tr.close()


if __name__ == "__main__":
    sys.exit(main())
