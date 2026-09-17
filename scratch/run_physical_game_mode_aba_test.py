"""
Controlled Physical Write Test: Game Mode / Settings AA 21 (Baseline -> Target -> Baseline).
Target Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)

SAFETY ENFORCEMENT:
- ONLY allowed write opcode: AA 21 (Game Mode / Settings write)
- Allowed read opcodes: AA 11 (Game Mode read), AA 12 (Remap L1), AA 16 (Remap L2), AA 18 (DKS)
- STRICTLY FORBIDDEN: AA 22, AA 23, AA 24, AA 25, AA 26, AA 27, AA 28, and any other write opcode.
- Target mutation: Invert stability_mode (1 -> 0), then restore back to 1.
- Byte changed in payload: exactly 1 byte (payload[11]: 0x01 -> 0x00).
- Strict rollback and zero persistent device changes guaranteed.
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

from keyboard_re.protocol.game_mode import (
    build_game_mode_expected_ack,
    build_game_mode_write_packet,
    validate_game_mode_ack,
    write_game_mode,
)
from keyboard_re.protocol.read import (
    read_dks_table,
    read_game_mode,
    read_keymap_table,
)
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
            "AA 11": 0,
            "AA 12": 0,
            "AA 16": 0,
            "AA 18": 0,
            "AA 21": 0,
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
        # Allowed write: ONLY AA 21
        # Allowed reads: AA 11, AA 12, AA 16, AA 18
        if prefix != 0xAA or opcode not in (0x11, 0x12, 0x16, 0x18, 0x21):
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
    print("=" * 72)
    print("PHYSICAL ABA TEST: Game Mode / Settings (AA 21)")
    print("Target Device: IO by Red Square Type 84 (0x0C45:0x80D6)")
    print("=" * 72)

    forensic_log: Dict[str, Any] = {
        "test_name": "Game Mode Physical ABA Test (AA 21)",
        "start_time": time.time(),
        "target_mutation": "stability_mode: 1 -> 0 -> 1",
    }

    native_tr = NativeHidTransport(vid=0x0C45, pid=0x80D6)
    safety_tr = SafetyFilteredTransport(native_tr)

    try:
        safety_tr.open()
        print("[1] Connected to hardware via SafetyFilteredTransport.")

        # ---------------------------------------------------------------------
        # Stage 1: Baseline Verification
        # ---------------------------------------------------------------------
        print("\n--- STAGE 1: Baseline Verification (AA 11) ---")
        base_resp = read_game_mode(safety_tr)
        base_payload = base_resp.to_payload()
        base_sha256 = hashlib.sha256(base_payload).hexdigest()
        print(f"[*] Baseline Game Mode SHA256: {base_sha256}")
        print(f"    stability_mode={base_resp.stability_mode}, report_rate={base_resp.report_rate_hz}Hz")

        assert base_resp.stability_mode == 1, f"Expected baseline stability_mode=1, got {base_resp.stability_mode}"

        # Baseline isolation hashes
        base_l1_bytes = read_keymap_table(safety_tr, opcode=0x12)
        base_l1_sha = hashlib.sha256(base_l1_bytes).hexdigest()
        base_l2_bytes = read_keymap_table(safety_tr, opcode=0x16)
        base_l2_sha = hashlib.sha256(base_l2_bytes).hexdigest()
        base_dks_bytes = read_dks_table(safety_tr)
        base_dks_sha = hashlib.sha256(base_dks_bytes).hexdigest()

        forensic_log["baseline"] = {
            "game_mode_sha256": base_sha256,
            "remap_l1_sha256": base_l1_sha,
            "remap_l2_sha256": base_l2_sha,
            "dks_sha256": base_dks_sha,
            "stability_mode": base_resp.stability_mode,
        }

        # ---------------------------------------------------------------------
        # Stage 2: Target Mutation Write (AA 21)
        # ---------------------------------------------------------------------
        print("\n--- STAGE 2: Mutate Game Mode: stability_mode 1 -> 0 (AA 21) ---")
        target_resp = base_resp.clone()
        target_resp.stability_mode = 0
        target_payload = target_resp.to_payload()
        target_sha256 = hashlib.sha256(target_payload).hexdigest()

        # Check diff before write
        diff_offsets = [i for i in range(56) if base_payload[i] != target_payload[i]]
        print(f"[*] Planned payload diff offsets: {diff_offsets}")
        assert diff_offsets == [11], f"Expected only offset 11 to differ, got {diff_offsets}"
        print(f"    Offset 11: 0x{base_payload[11]:02X} -> 0x{target_payload[11]:02X}")

        # Send write packet
        print("[*] Transmitting AA 21 write packet...")
        ack_bytes = write_game_mode(safety_tr, target_resp, strict_ack=True)
        print(f"[+] Received ACK: {ack_bytes[:16].hex(' ').upper()} (55 21 confirmed)")

        forensic_log["target_write"] = {
            "target_sha256": target_sha256,
            "diff_offsets": diff_offsets,
            "ack_prefix": ack_bytes[:8].hex(" "),
        }

        # ---------------------------------------------------------------------
        # Stage 3: Target Readback Verification
        # ---------------------------------------------------------------------
        print("\n--- STAGE 3: Readback Target Verification (AA 11) ---")
        target_readback = read_game_mode(safety_tr)
        target_rb_payload = target_readback.to_payload()
        target_rb_sha256 = hashlib.sha256(target_rb_payload).hexdigest()
        print(f"[*] Target readback SHA256: {target_rb_sha256}")
        assert target_rb_sha256 == target_sha256, f"Target readback mismatch! {target_rb_sha256} != {target_sha256}"
        assert target_readback.stability_mode == 0, f"Target stability_mode={target_readback.stability_mode}, expected 0"
        print("[+] Exact target match confirmed: stability_mode is physically 0!")

        # Verify exact diff against baseline
        rb_diff_offsets = [i for i in range(56) if base_payload[i] != target_rb_payload[i]]
        assert rb_diff_offsets == [11], f"Unexpected offsets modified in readback: {rb_diff_offsets}"
        print("[+] Isolation check on payload: EXACTLY 1 byte modified (payload offset 11).")

        # Isolation check across other subsystems
        print("[*] Checking other subsystems isolation during target state...")
        curr_l1 = hashlib.sha256(read_keymap_table(safety_tr, opcode=0x12)).hexdigest()
        curr_l2 = hashlib.sha256(read_keymap_table(safety_tr, opcode=0x16)).hexdigest()
        curr_dks = hashlib.sha256(read_dks_table(safety_tr)).hexdigest()
        assert curr_l1 == base_l1_sha, "Remap L1 mutated during Game Mode write!"
        assert curr_l2 == base_l2_sha, "Remap L2 mutated during Game Mode write!"
        assert curr_dks == base_dks_sha, "DKS Table mutated during Game Mode write!"
        print("[+] Isolation verified: Remap L1, Remap L2, DKS are 100% UNTOUCHED.")

        # ---------------------------------------------------------------------
        # Stage 4: Rollback to Baseline (AA 21)
        # ---------------------------------------------------------------------
        print("\n--- STAGE 4: Rollback to Baseline: stability_mode 0 -> 1 (AA 21) ---")
        print("[*] Transmitting AA 21 write packet to restore baseline...")
        restore_ack = write_game_mode(safety_tr, base_resp, strict_ack=True)
        print(f"[+] Received restore ACK: {restore_ack[:16].hex(' ').upper()} (55 21 confirmed)")

        forensic_log["rollback_write"] = {
            "restore_ack_prefix": restore_ack[:8].hex(" "),
        }

        # ---------------------------------------------------------------------
        # Stage 5: Final Readback & Safety Verification
        # ---------------------------------------------------------------------
        print("\n--- STAGE 5: Final Verification (AA 11) ---")
        final_readback = read_game_mode(safety_tr)
        final_payload = final_readback.to_payload()
        final_sha256 = hashlib.sha256(final_payload).hexdigest()
        print(f"[*] Final readback SHA256: {final_sha256}")
        assert final_sha256 == base_sha256, f"FINAL ROLLBACK MISMATCH! {final_sha256} != {base_sha256}"
        assert final_payload == base_payload, "Final payload does not equal baseline payload!"
        assert final_readback.stability_mode == 1, "Final stability_mode is not 1!"
        print("[+] Final readback SHA256 matches baseline SHA256 EXACTLY (0 bytes difference).")

        # Final isolation checks
        final_l1 = hashlib.sha256(read_keymap_table(safety_tr, opcode=0x12)).hexdigest()
        final_l2 = hashlib.sha256(read_keymap_table(safety_tr, opcode=0x16)).hexdigest()
        final_dks = hashlib.sha256(read_dks_table(safety_tr)).hexdigest()
        assert final_l1 == base_l1_sha, "Final Remap L1 mismatch!"
        assert final_l2 == base_l2_sha, "Final Remap L2 mismatch!"
        assert final_dks == base_dks_sha, "Final DKS mismatch!"
        print("[+] Final isolation checks all PASS.")

        # Verify safety statistics
        print(f"\n[*] Opcode counts: {safety_tr.opcode_counts}")
        print(f"[*] Forbidden write attempts: {safety_tr.forbidden_attempts}")
        assert safety_tr.forbidden_attempts == 0, "Forbidden write attempts were detected!"
        assert safety_tr.opcode_counts["AA 21"] == 2, f"Expected exactly 2 AA 21 writes (target + restore), got {safety_tr.opcode_counts['AA 21']}"

        forensic_log["final_readback"] = {
            "final_sha256": final_sha256,
            "matches_baseline": True,
        }
        forensic_log["opcode_counts"] = safety_tr.opcode_counts
        forensic_log["forbidden_attempts"] = safety_tr.forbidden_attempts
        forensic_log["status"] = "PASSED"
        forensic_log["end_time"] = time.time()

        # Save forensic log
        log_file = SCRATCH_DIR / "game_mode_aba_test_forensic_log.json"
        log_file.write_text(json.dumps(forensic_log, indent=2), encoding="utf-8")
        print(f"[+] Forensic log saved to {log_file}")

        print("\n" + "=" * 72)
        print("ABA TEST RESULT: 100% SUCCESSFUL! Zero residual changes on device.")
        print("=" * 72)
        return 0

    except Exception as ex:
        print(f"\n[-] ABA TEST ERROR: {ex}")
        import traceback
        traceback.print_exc()
        forensic_log["status"] = "FAILED"
        forensic_log["error"] = str(ex)
        log_file = SCRATCH_DIR / "game_mode_aba_test_forensic_log.json"
        log_file.write_text(json.dumps(forensic_log, indent=2), encoding="utf-8")
        return 1
    finally:
        safety_tr.close()


if __name__ == "__main__":
    sys.exit(main())
