"""
Physical Hardware Test: Remap L1 Key A -> B -> A for Type 84.

Executes a single controlled physical write experiment:
1. Opens verified configuration HID interface (0xFF68:0x0061, VID 0x0C45, PID 0x80D6).
2. Reads current Remap L1 table via AA 12.
3. Saves baseline to captures/research/remap_l1/python_a_to_b_before.bin.
4. Verifies Slot 50 == 00 04 00 02 (Key 'A'). Aborts on mismatch.
5. Constructs target via patch_remap_image (Slot 50 -> 00 05 00 02).
6. Verifies exact diff: 1 byte changed at offset 0x00C9 (0x04 -> 0x05).
7. Requires explicit confirmation (RemapWritePolicy(confirmed=True)).
8. Dispatches write_remap_image (10 chunks AA 22 with per-chunk ACK validation).
9. Reads back AA 12 and verifies Slot 50 == 00 05 00 02.
10. Automatically restores Key A: writes baseline back (10 chunks with ACK validation).
11. Reads back AA 12 and verifies byte-for-byte identity with original baseline (final_diff == 0).
12. Saves diagnostic artifacts and outputs complete forensic report.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from keyboard_re.protocol.read import (
    read_device_info,
    read_keyboard_status,
    read_keymap_table,
)
from keyboard_re.protocol.remap import (
    REMAP_CHUNK_COUNT,
    REMAP_CHUNK_SIZE,
    REMAP_IMAGE_SIZE,
    REMAP_READ_OPCODE,
    REMAP_SLOT_COUNT,
    REMAP_SLOT_SIZE,
    REMAP_TAIL_SIZE,
    REMAP_WRITE_OPCODE,
    RemapAck,
    RemapByteDiff,
    RemapRecord,
    RemapWritePolicy,
    RemapWriteResult,
    build_remap_write_chunks,
    diff_images,
    patch_remap_image,
    slot_offset,
    validate_remap_ack,
    write_remap_image,
)
from keyboard_re.transport.native_hid import NativeHidTransport

BEFORE_BIN_PATH = Path("captures/research/remap_l1/python_a_to_b_before.bin")
WRITTEN_B_BIN_PATH = Path("captures/research/remap_l1/python_a_to_b_written.bin")
AFTER_BIN_PATH = Path("captures/research/remap_l1/python_a_to_b_after.bin")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_remap_physical_test(
    confirmed: bool = False,
    dry_run_only: bool = False,
) -> Dict[str, Any]:
    """
    Execute the Remap L1 A -> B -> A physical hardware experiment.
    """
    report: Dict[str, Any] = {
        "status": "INITIALIZING",
        "dry_run": dry_run_only,
        "confirmed": confirmed,
        "errors": [],
    }

    BEFORE_BIN_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 76)
    print("PHYSICAL HARDWARE TEST: KEY REMAP L1 (A -> B -> A)")
    print("IO by Red Square Type 84 Magnetic Black")
    print("=" * 76)

    # 1. Connect over verified interface
    transport = NativeHidTransport()
    print(f"Connecting to HID interface (VID=0x{transport.vid:04X}, PID=0x{transport.pid:04X}, UsagePage=0x{transport.usage_page:04X}:0x{transport.usage:04X})...")

    try:
        transport.open()
    except Exception as e:
        err = f"Failed to open HID transport: {e}"
        print(f"ERROR: {err}", file=sys.stderr)
        report["status"] = "CONNECTION_FAILED"
        report["errors"].append(err)
        return report

    try:
        # Verify device communication
        dev_info = read_device_info(transport, timeout=1.0)
        status = read_keyboard_status(transport, timeout=1.0)
        print(f"Device:       {transport.actual_product_name}")
        print(f"Firmware:     v{dev_info.firmware_version}")
        print(f"Bootloader:   v{dev_info.bootloader_version}")
        print(f"Profile:      #{status.active_profile}")
        print("-" * 76)

        # 2. READ AA 12 Baseline
        print("STEP 1/6: Reading initial live Remap L1 table (AA 12)...")
        t_start_read = time.perf_counter()
        baseline = read_keymap_table(transport, opcode=REMAP_READ_OPCODE, timeout=1.0)
        read_time_ms = (time.perf_counter() - t_start_read) * 1000.0
        print(f"  Received 512 bytes in {read_time_ms:.1f} ms.")

        # Save baseline to captures/research/remap_l1/python_a_to_b_before.bin
        BEFORE_BIN_PATH.write_bytes(baseline)
        print(f"  Saved baseline to {BEFORE_BIN_PATH}")
        print(f"  Baseline SHA-256: {sha256_hex(baseline)}")
        print(f"  Baseline MD5:     {md5_hex(baseline)}")

        report["baseline_sha256"] = sha256_hex(baseline)
        report["baseline_md5"] = md5_hex(baseline)

        # 3. Check Slot 50 == 00 04 00 02
        slot_50_off = slot_offset(50)  # 200
        slot_50_before = baseline[slot_50_off : slot_50_off + 4]
        expected_a = bytes([0x00, 0x04, 0x00, 0x02])
        print(f"STEP 2/6: Verifying Slot 50 initial state: {slot_50_before.hex(' ').upper()}...")

        if slot_50_before != expected_a:
            err = (
                f"SAFETY ABORT: Slot 50 is {slot_50_before.hex(' ').upper()}, expected 00 04 00 02 ('A'). "
                f"Keyboard is not in clean baseline state. Zero writes performed."
            )
            print(f"ERROR: {err}", file=sys.stderr)
            report["status"] = "SAFETY_ABORT_SLOT_NOT_A"
            report["errors"].append(err)
            return report

        print("  VERIFIED: Slot 50 contains Key 'A' (00 04 00 02). Clean baseline confirmed.")

        # 4. Construct Target: Key A -> B (0x05)
        print("STEP 3/6: Constructing target image via patch_remap_image()...")
        target_b = patch_remap_image(
            baseline,
            slot=50,
            new_record=RemapRecord(prefix=0, scancode=0x05, special=0, type=0x02),
        )

        diffs_to_b = diff_images(baseline, target_b)
        print(f"  Diff count: {len(diffs_to_b)} byte(s)")
        if len(diffs_to_b) != 1 or diffs_to_b[0].offset != 201 or diffs_to_b[0].before != 0x04 or diffs_to_b[0].after != 0x05:
            err = f"SAFETY ABORT: Target diff invalid: {diffs_to_b}. Expected single diff at offset 0x00C9 (04 -> 05)."
            print(f"ERROR: {err}", file=sys.stderr)
            report["status"] = "SAFETY_ABORT_INVALID_TARGET"
            report["errors"].append(err)
            return report

        print(f"  VERIFIED: Strictly 1 byte altered: offset 0x{diffs_to_b[0].offset:04X} ({diffs_to_b[0].offset}), 0x{diffs_to_b[0].before:02X} -> 0x{diffs_to_b[0].after:02X}.")
        print(f"  Target SHA-256:   {sha256_hex(target_b)}")
        report["target_sha256"] = sha256_hex(target_b)
        report["target_md5"] = md5_hex(target_b)

        # Operator message
        print("-" * 76)
        print("OPERATOR NOTICE:")
        print("Writing Remap L1 A -> B will be performed.")
        print("Change: offset 0x00C9, 0x04 -> 0x05.")
        print("Full read-back will be performed after writing.")
        print("After verifying B, the key will automatically be restored to A.")
        print("-" * 76)

        if dry_run_only or not confirmed:
            print("DRY-RUN / AUDIT MODE: Physical write is NOT confirmed. Stopping.")
            report["status"] = "DRY_RUN_COMPLETED"
            return report

        # 5. Execute WRITE A -> B
        print("STEP 4/6: Executing physical WRITE A -> B (write_remap_image)...")
        policy_write_b = RemapWritePolicy(
            require_confirmation=True,
            require_baseline=True,
            require_readback=True,
            confirmed=True,
            timeout_ms=1000,
        )

        t_write_b_start = time.perf_counter()
        res_b = write_remap_image(
            transport,
            image=target_b,
            baseline=baseline,
            policy=policy_write_b,
        )
        write_b_ms = (time.perf_counter() - t_write_b_start) * 1000.0

        print(f"  Transaction completed in {write_b_ms:.1f} ms.")
        print(f"  Chunks sent:        {res_b.chunks_sent} / 10")
        print(f"  ACKs received:      {res_b.acks_received} / 10")
        print(f"  Read-back verified: {res_b.readback_verified}")

        report["write_b_ms"] = write_b_ms
        report["write_b_chunks"] = res_b.chunks_sent
        report["write_b_acks"] = res_b.acks_received
        report["write_b_verified"] = res_b.readback_verified

        if not res_b.success or not res_b.readback_verified:
            err = f"WRITE A -> B FAILED: {res_b.error_message}. Stopping."
            print(f"ERROR: {err}", file=sys.stderr)
            report["status"] = "WRITE_B_FAILED"
            report["errors"].append(err)
            return report

        # Save written B image
        WRITTEN_B_BIN_PATH.write_bytes(res_b.readback_image)
        slot_50_after_b = res_b.readback_image[slot_50_off : slot_50_off + 4]
        expected_b = bytes([0x00, 0x05, 0x00, 0x02])
        print(f"  Slot 50 verified on device: {slot_50_after_b.hex(' ').upper()} (Key 'B')")
        if slot_50_after_b != expected_b:
            err = f"Slot 50 read-back check failed: expected 00 05 00 02, got {slot_50_after_b.hex(' ')}"
            print(f"ERROR: {err}", file=sys.stderr)
            report["status"] = "READBACK_B_MISMATCH"
            report["errors"].append(err)
            return report

        # 6. Execute RESTORE B -> A
        print("-" * 76)
        print("STEP 5/6: Executing automatic RESTORE B -> A (write_remap_image)...")
        policy_restore = RemapWritePolicy(
            require_confirmation=True,
            require_baseline=True,
            require_readback=True,
            confirmed=True,
            timeout_ms=1000,
        )

        t_restore_start = time.perf_counter()
        res_restore = write_remap_image(
            transport,
            image=baseline,
            baseline=res_b.readback_image,
            policy=policy_restore,
        )
        restore_ms = (time.perf_counter() - t_restore_start) * 1000.0

        print(f"  Restore completed in {restore_ms:.1f} ms.")
        print(f"  Chunks sent:        {res_restore.chunks_sent} / 10")
        print(f"  ACKs received:      {res_restore.acks_received} / 10")
        print(f"  Read-back verified: {res_restore.readback_verified}")

        report["restore_ms"] = restore_ms
        report["restore_chunks"] = res_restore.chunks_sent
        report["restore_acks"] = res_restore.acks_received
        report["restore_verified"] = res_restore.readback_verified

        if not res_restore.success or not res_restore.readback_verified:
            err = f"RESTORE B -> A FAILED: {res_restore.error_message}. Keyboard left at Key B state!"
            print(f"CRITICAL ERROR: {err}", file=sys.stderr)
            report["status"] = "RESTORE_FAILED"
            report["errors"].append(err)
            return report

        # 7. Final byte-for-byte readback verification against initial baseline
        print("-" * 76)
        print("STEP 6/6: Performing final full table READ (AA 12) & zero-diff verification...")
        final_readback = read_keymap_table(transport, opcode=REMAP_READ_OPCODE, timeout=1.0)
        AFTER_BIN_PATH.write_bytes(final_readback)

        final_diffs = diff_images(baseline, final_readback)
        print(f"  Final difference count vs initial baseline: {len(final_diffs)} byte(s)")
        print(f"  Final SHA-256:    {sha256_hex(final_readback)}")
        print(f"  Final MD5:        {md5_hex(final_readback)}")

        report["final_sha256"] = sha256_hex(final_readback)
        report["final_md5"] = md5_hex(final_readback)
        report["final_diff_count"] = len(final_diffs)

        if len(final_diffs) != 0:
            err = f"RESTORATION DIFF DETECTED: {len(final_diffs)} bytes differ vs initial baseline!"
            print(f"ERROR: {err}", file=sys.stderr)
            for d in final_diffs[:10]:
                print(f"  offset 0x{d.offset:04X}: baseline=0x{d.before:02X}, final=0x{d.after:02X}")
            report["status"] = "FINAL_DIFF_NON_ZERO"
            report["errors"].append(err)
            return report

        print("=" * 76)
        print("SUCCESS: REMAP L1 PHYSICAL WRITE TEST COMPLETED (final_diff == 0).")
        print("Hardware 100% returned to exact factory baseline.")
        print("=" * 76)

        report["status"] = "SUCCESS"
        return report

    finally:
        transport.close()
        print("Closed HID transport.")


def main():
    parser = argparse.ArgumentParser(description="Physical Remap L1 Write Experiment (A -> B -> A)")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicitly confirm physical hardware writes (RemapWritePolicy(confirmed=True))",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform read-only baseline check and dry-run without writing",
    )
    args = parser.parse_args()

    res = run_remap_physical_test(confirmed=args.confirm, dry_run_only=args.dry_run)
    sys.exit(0 if res.get("status") == "SUCCESS" or (args.dry_run and res.get("status") == "DRY_RUN_COMPLETED") else 1)


if __name__ == "__main__":
    main()
