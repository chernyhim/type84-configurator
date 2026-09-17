"""
Physical Preflight: Read-Only Hall / Rapid Trigger (AA 17) & Subsystem Baselines.
Device: IO by Red Square Type 84 Magnetic Black (0x0C45:0x80D6)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

WORKSPACE_ROOT = Path(r"c:\KeyboardSoft")
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from keyboard_re.models.base import (
    KEY_MAP,
    key_address,
)
from keyboard_re.protocol.hall import (
    HallKeyConfig,
    decode_hall_image,
)
from keyboard_re.protocol.read import (
    read_dks_table,
    read_game_mode,
    read_hall_profile,
    read_keymap_table,
)
from keyboard_re.transport.native_hid import NativeHidTransport

SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


def main():
    print("=" * 70)
    print("PHYSICAL READ-ONLY PREFLIGHT: HALL / RAPID TRIGGER (AA 17)")
    print("=" * 70)

    transport = NativeHidTransport()
    try:
        print("[1/5] Connecting to physical HID keyboard (VID 0x0C45, PID 0x80D6)...")
        transport.open()
        print("  -> Connected successfully.")

        print("\n[2/5] Draining any stale reports in input buffer...")
        drained = transport.drain_input_buffer()
        print(f"  -> Drained {drained} stale reports.")

        print("\n[3/5] Reading Hall Profile 1 (AA 17, 18 chunks + terminator)...")
        hall_bytes = read_hall_profile(transport, profile_id=1, timeout=1.0)
        hall_sha = hashlib.sha256(hall_bytes).hexdigest()
        print(f"  -> Successfully read 1008 bytes.")
        print(f"  -> Hall Profile SHA-256: {hall_sha}")

        # Save baseline bin
        baseline_bin_path = SCRATCH_DIR / "physical_hall_baseline.bin"
        baseline_bin_path.write_bytes(hall_bytes)
        print(f"  -> Saved raw baseline to {baseline_bin_path}")

        print("\n[4/5] Reading baseline state of other subsystems for non-interference check...")
        # L1 Keymap
        l1_bytes = read_keymap_table(transport, opcode=0x12, timeout=1.0)
        l1_sha = hashlib.sha256(l1_bytes).hexdigest()

        # L2 Keymap
        l2_bytes = read_keymap_table(transport, opcode=0x16, timeout=1.0)
        l2_sha = hashlib.sha256(l2_bytes).hexdigest()

        # DKS
        dks_bytes = read_dks_table(transport, timeout=1.0)
        dks_sha = hashlib.sha256(dks_bytes).hexdigest()

        # Game Mode
        gm_resp = read_game_mode(transport, timeout=1.0)
        gm_bytes = bytes(gm_resp.raw_payload) if hasattr(gm_resp, "raw_payload") else bytes(str(gm_resp), "utf-8")
        gm_sha = hashlib.sha256(gm_bytes).hexdigest()

        print(f"  -> Remap L1 (AA 12) SHA-256: {l1_sha}")
        print(f"  -> Remap L2 (AA 16) SHA-256: {l2_sha}")
        print(f"  -> DKS (AA 18)      SHA-256: {dks_sha}")
        print(f"  -> Game Mode (AA 11) SHA-256: {gm_sha}")

        print("\n[5/5] Inspecting decoded Hall keys (dense layout & canonical <BBHHH)...")
        decoded = decode_hall_image(hall_bytes)

        inspect_keys = ["A", "S", "D", "SPACE", "Q", "Z", "ENTER"]
        key_details = {}

        for kname in inspect_keys:
            bank, col = KEY_MAP[kname]
            addr = key_address(bank, col)
            raw_rec = hall_bytes[addr : addr + 8]
            cfg = decoded.get(kname)

            axis_type = raw_rec[0]
            flags = raw_rec[1]
            act_raw, rt_press_raw, rt_release_raw = struct.unpack_from("<HHH", raw_rec, 2)

            key_details[kname] = {
                "bank": bank,
                "col": col,
                "address": addr,
                "address_hex": f"0x{addr:04X}",
                "raw_hex": raw_rec.hex(" ").upper(),
                "axis_type": axis_type,
                "flags": flags,
                "flags_hex": f"0x{flags:02X}",
                "is_rt_enabled": bool(flags & 0x01),
                "actuation_mm": act_raw / 100.0,
                "rt_press_mm": rt_press_raw / 100.0,
                "rt_release_mm": rt_release_raw / 100.0,
            }

            print(f"  Key '{kname:5s}' @ Addr 0x{addr:04X} (Bank {bank}, Col {col:2d}):")
            print(f"    Raw bytes : {raw_rec.hex(' ').upper()}")
            print(f"    axis_type : {axis_type}")
            print(f"    flags     : 0x{flags:02X} (RT Enabled: {bool(flags & 0x01)})")
            print(f"    actuation : {act_raw / 100.0:.2f} mm (raw {act_raw})")
            print(f"    rt_press  : {rt_press_raw / 100.0:.2f} mm (raw {rt_press_raw})")
            print(f"    rt_rel    : {rt_release_raw / 100.0:.2f} mm (raw {rt_release_raw})")

        preflight_data = {
            "hall_sha256": hall_sha,
            "remap_l1_sha256": l1_sha,
            "remap_l2_sha256": l2_sha,
            "dks_sha256": dks_sha,
            "game_mode_sha256": gm_sha,
            "keys": key_details,
        }

        info_path = SCRATCH_DIR / "physical_hall_preflight_info.json"
        info_path.write_text(json.dumps(preflight_data, indent=2), encoding="utf-8")
        print(f"\nPreflight report written to {info_path}")

    finally:
        transport.close()
        print("\nDevice disconnected cleanly.")


if __name__ == "__main__":
    main()
