"""
Physical ABA Test: Hall Effect / Rapid Trigger (AA 27)
Safety Policy: ONLY opcode AA 27 write allowed. All other write opcodes forbidden.
Closed-loop verification: Baseline -> RT ON -> Baseline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import time
from typing import Any, Dict, List

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.models.base import KEY_MAP, key_address
from keyboard_re.protocol.hall import (
    HALL_CHUNK_COUNT,
    HALL_CHUNK_PAYLOAD_SIZE,
    HALL_IMAGE_SIZE,
    decode_hall_image,
)
from keyboard_re.protocol.plan import (
    WriteChunk,
    build_hall_write_chunks,
)
from keyboard_re.protocol.read import (
    read_dks_table,
    read_game_mode,
    read_hall_profile,
    read_keymap_table,
)
from keyboard_re.transport.native_hid import NativeHidTransport

SCRATCH_DIR = WORKSPACE_ROOT / "scratch"


class SafetyFilteredTransport:
    """Wrapper that intercepts every outgoing packet and ensures STRICTLY controlled opcodes."""

    def __init__(self, backend: NativeHidTransport):
        self.backend = backend
        self.sent_packets: List[Dict[str, Any]] = []
        self.received_reports: List[Dict[str, Any]] = []
        self.opcode_counts: Dict[str, int] = {
            "AA 17": 0,
            "AA 27": 0,
            "AA 11": 0,
            "AA 12": 0,
            "AA 16": 0,
            "AA 18": 0,
        }
        self.forbidden_attempts: int = 0

    def open(self) -> None:
        self.backend.open()

    def close(self) -> None:
        self.backend.close()

    @property
    def is_connected(self) -> bool:
        return self.backend.is_connected

    def drain_input_buffer(self, max_reports: int = 512) -> int:
        return self.backend.drain_input_buffer(max_reports=max_reports)

    def send_report(self, report_id: int, data: bytes | bytearray) -> None:
        pkt = bytes(data)
        if len(pkt) != 64:
            raise ValueError(f"Invalid packet length: {len(pkt)}")

        prefix = pkt[0]
        opcode = pkt[1]
        opcode_str = f"{prefix:02X} {opcode:02X}"

        # STRICT WHITELIST: ONLY AA 27 (write) or allowed read opcodes
        ALLOWED_OPCODES = {"AA 27", "AA 17", "AA 11", "AA 12", "AA 16", "AA 18"}
        if opcode_str not in ALLOWED_OPCODES:
            self.forbidden_attempts += 1
            err = (
                f"SAFETY INTERCEPTOR BLOCKED FORBIDDEN PACKET: {opcode_str} "
                f"(full header: {pkt[:8].hex(' ').upper()})! "
                f"ONLY AA 27 write is allowed in this test!"
            )
            print(f"\n[CRITICAL ERROR] {err}", flush=True)
            raise RuntimeError(err)

        self.opcode_counts[opcode_str] = self.opcode_counts.get(opcode_str, 0) + 1
        self.sent_packets.append({
            "timestamp": time.monotonic(),
            "opcode": opcode_str,
            "header": pkt[:8].hex(" ").upper(),
            "length": len(pkt),
        })

        self.backend.send_report(report_id, pkt)

    def receive_report(
        self,
        timeout: float = 1.0,
        expected_opcode: int | None = None,
        expected_address: int | None = None,
    ) -> bytes:
        rep = self.backend.receive_report(
            timeout=timeout,
            expected_opcode=expected_opcode,
            expected_address=expected_address,
        )
        self.received_reports.append({
            "timestamp": time.monotonic(),
            "header": rep[:8].hex(" ").upper(),
            "length": len(rep),
        })
        return rep


def vendor_js_decode_slot(raw_slot: bytes) -> Dict[str, Any]:
    """
    Simulates the EXACT vendor JS decoder logic from https://web.io.vision/ (function Xo):
    u = r[y] (axisType)
    f = r[y+1] (flags)
    C = (f & 1) !== 0 (isWholeFast / Rapid Trigger)
    b = (f & 2) !== 0 (isRampageMode)
    w = r[y+2] | r[y+3] << 8 (triggerKeyStroke)
    N = r[y+4] | r[y+5] << 8 (pressRT)
    _ = r[y+6] | r[y+7] << 8 (releaseRT)
    """
    axis_type = raw_slot[0]
    flags = raw_slot[1]
    is_whole_fast = (flags & 1) != 0
    is_rampage_mode = (flags & 2) != 0
    trigger_raw = raw_slot[2] | (raw_slot[3] << 8)
    press_raw = raw_slot[4] | (raw_slot[5] << 8)
    release_raw = raw_slot[6] | (raw_slot[7] << 8)

    return {
        "axisType": axis_type,
        "flags": flags,
        "isWholeFast": is_whole_fast,
        "isRampageMode": is_rampage_mode,
        "triggerKeyStroke_mm": round(trigger_raw / 100.0, 2),
        "pressRT_mm": round(press_raw / 100.0, 2),
        "releaseRT_mm": round(release_raw / 100.0, 2),
    }


def execute_hall_write_chunks(
    transport: SafetyFilteredTransport,
    chunks: List[WriteChunk],
    timeout: float = 1.0,
) -> None:
    """Execute write chunks sequentially with ACK validation and drain."""
    transport.drain_input_buffer()
    for chunk in chunks:
        expected_opcode = chunk.expected_ack[1]  # 0x27
        expected_addr = chunk.address

        transport.send_report(0, chunk.packet)
        ack = transport.receive_report(
            timeout=timeout,
            expected_opcode=expected_opcode,
            expected_address=expected_addr,
        )

        # Validate ACK
        if len(ack) != 64:
            raise ValueError(f"Chunk addr 0x{chunk.address:04X}: Invalid ACK length {len(ack)}")
        if ack[0] != 0x55 or ack[1] != 0x27:
            raise ValueError(
                f"Chunk addr 0x{chunk.address:04X}: Expected prefix 55 27, got {ack[:2].hex(' ').upper()}"
            )
        ack_addr = struct.unpack_from("<H", ack, 3)[0] if len(ack) >= 5 else None
        if ack_addr != chunk.address:
            raise ValueError(
                f"Chunk addr 0x{chunk.address:04X}: ACK address mismatch: expected 0x{chunk.address:04X}, got 0x{ack_addr:04X}"
            )


def run_aba_test():
    print("=" * 75)
    print("CONTROLLED PHYSICAL ABA TEST: HALL EFFECT / RAPID TRIGGER (AA 27)")
    print("=" * 75)

    backend = NativeHidTransport()
    transport = SafetyFilteredTransport(backend)
    transport.open()

    log_record = {
        "test": "Physical Hall/RT ABA (RT OFF -> RT ON -> RT OFF)",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": [],
        "success": False,
    }

    try:
        # 1. READ BASELINE & PRE-CHECK
        print("\n[STEP 1] Reading baseline state...")
        transport.drain_input_buffer()
        baseline_bytes = read_hall_profile(transport, profile_id=1, timeout=1.0)
        baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
        print(f"  -> Hall Baseline SHA-256: {baseline_sha}")

        # Baseline check against preflight
        preflight_bin = SCRATCH_DIR / "physical_hall_baseline.bin"
        if preflight_bin.exists():
            expected_sha = hashlib.sha256(preflight_bin.read_bytes()).hexdigest()
            if baseline_sha != expected_sha:
                raise RuntimeError(
                    f"Baseline SHA mismatch with preflight! Expected {expected_sha}, got {baseline_sha}"
                )
            print("  -> Baseline SHA matches preflight exactly.")

        # Key S address and baseline state
        bank, col = KEY_MAP["S"]
        s_addr = key_address(bank, col)  # 400 = 0x0190
        s_baseline_raw = baseline_bytes[s_addr : s_addr + 8]
        print(f"  -> Target Key 'S' @ Addr 0x{s_addr:04X} (Bank {bank}, Col {col}):")
        print(f"     Raw baseline: {s_baseline_raw.hex(' ').upper()}")

        s_vendor = vendor_js_decode_slot(s_baseline_raw)
        print(f"     Vendor JS state: isWholeFast={s_vendor['isWholeFast']}, actuation={s_vendor['triggerKeyStroke_mm']}mm, pressRT={s_vendor['pressRT_mm']}mm, releaseRT={s_vendor['releaseRT_mm']}mm")
        if s_vendor["isWholeFast"]:
            raise RuntimeError("Key S is already RT ON in baseline! Expected RT OFF.")

        # Baseline hashes of other subsystems
        l1_baseline = read_keymap_table(transport, opcode=0x12, timeout=1.0)
        l1_sha = hashlib.sha256(l1_baseline).hexdigest()
        l2_baseline = read_keymap_table(transport, opcode=0x16, timeout=1.0)
        l2_sha = hashlib.sha256(l2_baseline).hexdigest()
        dks_baseline = read_dks_table(transport, timeout=1.0)
        dks_sha = hashlib.sha256(dks_baseline).hexdigest()
        gm_baseline = read_game_mode(transport, timeout=1.0)
        gm_sha = hashlib.sha256(bytes(gm_baseline.raw_payload)).hexdigest()

        log_record["steps"].append({
            "step": 1,
            "name": "baseline_verification",
            "hall_sha256": baseline_sha,
            "key_s_raw": s_baseline_raw.hex(" ").upper(),
            "key_s_vendor_js": s_vendor,
        })

        # 2. WRITE TARGET: KEY S -> RT ON (flags=0x01, rt_press=0.20mm, rt_release=0.20mm)
        print("\n[STEP 2] Preparing target state: Key 'S' -> RT ON (0.20mm / 0.20mm)...")
        target_bytes = bytearray(baseline_bytes)

        # Encode Key S with RT enabled
        # <BBHHH: axis_type=0, flags=1, actuation=120 (1.20mm), rt_press=20 (0.20mm), rt_release=20 (0.20mm)
        target_s_raw = bytes([0x00, 0x01, 0x78, 0x00, 0x14, 0x00, 0x14, 0x00])
        target_bytes[s_addr : s_addr + 8] = target_s_raw
        target_sha = hashlib.sha256(target_bytes).hexdigest()

        # Build chunks and verify dry run
        target_chunks = build_hall_write_chunks(bytes(target_bytes))
        print(f"  -> Generated {len(target_chunks)} write chunks (18 data + 1 terminator).")
        print(f"  -> Target Key 'S' record: {target_s_raw.hex(' ').upper()}")

        print("\n[STEP 3] Executing physical write AA 27 for RT ON...")
        execute_hall_write_chunks(transport, target_chunks, timeout=1.0)
        print("  -> All 19 chunks sent and acknowledged with valid 55 27 ACKs.")

        # 3. READBACK AFTER ON
        print("\n[STEP 4] Executing readback AA 17 after RT ON...")
        time.sleep(0.05)
        readback_on = read_hall_profile(transport, profile_id=1, timeout=1.0)
        readback_on_sha = hashlib.sha256(readback_on).hexdigest()
        print(f"  -> Readback SHA-256: {readback_on_sha}")

        # Check Key S in readback
        s_readback_raw = readback_on[s_addr : s_addr + 8]
        print(f"  -> Key 'S' readback bytes: {s_readback_raw.hex(' ').upper()}")
        if s_readback_raw != target_s_raw:
            raise RuntimeError(
                f"Key S readback mismatch! Expected {target_s_raw.hex(' ').upper()}, got {s_readback_raw.hex(' ').upper()}"
            )

        # Verify through vendor JS decoding
        s_on_vendor = vendor_js_decode_slot(s_readback_raw)
        print(f"  -> Vendor JS decode: isWholeFast={s_on_vendor['isWholeFast']}, actuation={s_on_vendor['triggerKeyStroke_mm']}mm, pressRT={s_on_vendor['pressRT_mm']}mm, releaseRT={s_on_vendor['releaseRT_mm']}mm")
        if not s_on_vendor["isWholeFast"]:
            raise RuntimeError("Vendor JS decode: isWholeFast is False, expected True!")
        if s_on_vendor["pressRT_mm"] != 0.20:
            raise RuntimeError(f"Vendor JS decode: pressRT is {s_on_vendor['pressRT_mm']}mm, expected 0.20mm!")
        if s_on_vendor["releaseRT_mm"] != 0.20:
            raise RuntimeError(f"Vendor JS decode: releaseRT is {s_on_vendor['releaseRT_mm']}mm, expected 0.20mm!")

        # Verify that ONLY Key S changed across the ENTIRE 1008-byte image
        diff_indices = [idx for idx in range(1008) if baseline_bytes[idx] != readback_on[idx]]
        print(f"  -> Changed byte indices count: {len(diff_indices)} (expected exactly 3: flags, rt_press, rt_release)")
        expected_indices = [s_addr + 1, s_addr + 4, s_addr + 6]
        if diff_indices != expected_indices:
            raise RuntimeError(
                f"Unexpected byte differences outside Key S! Diff indices: {diff_indices}, expected: {expected_indices}"
            )
        print("  -> CONFIRMED: Strictly only Key S RT parameters changed in the physical keyboard!")

        log_record["steps"].append({
            "step": 2,
            "name": "write_rt_on_verified",
            "readback_sha256": readback_on_sha,
            "key_s_raw": s_readback_raw.hex(" ").upper(),
            "vendor_js_decode": s_on_vendor,
            "diff_indices": diff_indices,
        })

        # USER INSPECTION PAUSE (15 SECONDS)
        print("\n" + "=" * 70, flush=True)
        print(">>> PAUSE FOR WEB CONFIGURATOR INSPECTION (15 SECONDS) <<<", flush=True)
        print(">>> Device is currently in RT ON state for Key 'S' (0.20mm / 0.20mm).", flush=True)
        print(">>> You can check https://web.io.vision/ in your browser.", flush=True)
        print("=" * 70, flush=True)
        for remaining in range(15, 0, -1):
            print(f"  Holding RT ON state... {remaining}s remaining before auto-restore", flush=True)
            time.sleep(1.0)
        print(">>> Pause complete. Proceeding with automatic restore to RT OFF...\n", flush=True)

        # 4. RESTORE OFF
        print("\n[STEP 5] Restoring baseline state (Key 'S' -> RT OFF)...", flush=True)
        restore_chunks = build_hall_write_chunks(baseline_bytes)
        execute_hall_write_chunks(transport, restore_chunks, timeout=1.0)
        print("  -> All 19 restore chunks sent and acknowledged with valid 55 27 ACKs.")

        # 5. FINAL READBACK & BYTE-FOR-BYTE BASELINE MATCH
        print("\n[STEP 6] Executing final readback AA 17...")
        time.sleep(0.05)
        final_bytes = read_hall_profile(transport, profile_id=1, timeout=1.0)
        final_sha = hashlib.sha256(final_bytes).hexdigest()
        print(f"  -> Final SHA-256:    {final_sha}")
        print(f"  -> Baseline SHA-256: {baseline_sha}")

        if final_sha != baseline_sha:
            raise RuntimeError(
                f"FINAL READBACK DOES NOT MATCH BASELINE SHA! Baseline: {baseline_sha}, Final: {final_sha}"
            )
        if final_bytes != baseline_bytes:
            raise RuntimeError("FINAL READBACK BYTES DIFFER FROM BASELINE BYTES!")
        print("  -> CONFIRMED: Final state matches baseline byte-for-byte!")

        # 6. NON-INTERFERENCE VERIFICATION ACROSS OTHER SUBSYSTEMS
        print("\n[STEP 7] Checking other subsystems for non-interference...")
        l1_final = read_keymap_table(transport, opcode=0x12, timeout=1.0)
        l2_final = read_keymap_table(transport, opcode=0x16, timeout=1.0)
        dks_final = read_dks_table(transport, timeout=1.0)
        gm_final = read_game_mode(transport, timeout=1.0)

        assert hashlib.sha256(l1_final).hexdigest() == l1_sha, "Remap L1 modified!"
        assert hashlib.sha256(l2_final).hexdigest() == l2_sha, "Remap L2 modified!"
        assert hashlib.sha256(dks_final).hexdigest() == dks_sha, "DKS modified!"
        assert hashlib.sha256(bytes(gm_final.raw_payload)).hexdigest() == gm_sha, "Game Mode modified!"

        print("  -> Remap L1 (AA 12): UNCHANGED (SHA matched)")
        print("  -> Remap L2 (AA 16): UNCHANGED (SHA matched)")
        print("  -> DKS (AA 18):      UNCHANGED (SHA matched)")
        print("  -> Game Mode (AA 11): UNCHANGED (SHA matched)")

        # 7. SAFETY AUDIT
        print("\n[STEP 8] Safety Audit:")
        print(f"  -> Forbidden write attempts: {transport.forbidden_attempts}")
        print(f"  -> Sent opcode counts: {transport.opcode_counts}")
        assert transport.forbidden_attempts == 0, "Forbidden write attempts were recorded!"
        assert transport.opcode_counts["AA 27"] == 38, f"Expected exactly 38 AA 27 packets (19 ON + 19 OFF), got {transport.opcode_counts['AA 27']}"

        log_record["success"] = True
        log_record["steps"].append({
            "step": 3,
            "name": "restore_verified",
            "final_sha256": final_sha,
            "non_interference_all_passed": True,
            "forbidden_attempts": transport.forbidden_attempts,
            "opcode_counts": transport.opcode_counts,
        })

        out_log = SCRATCH_DIR / "physical_hall_aba_test_log.json"
        out_log.write_text(json.dumps(log_record, indent=2), encoding="utf-8")
        print(f"\nAudit log saved to {out_log}")
        print("\n" + "=" * 75)
        print("PHYSICAL ABA RE-VERIFICATION: 100% PASS")
        print("=" * 75)

    finally:
        transport.close()
        print("Device disconnected cleanly.")


if __name__ == "__main__":
    run_aba_test()
