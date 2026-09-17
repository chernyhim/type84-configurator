"""
Research runner for Rapid Trigger (RT) Release encoding on IO Type 84 Magnetic Black.

Executes a series of controlled RT Release modifications on a single key in Hall Profile 1:
- Uses NativeHidTransport (UsagePage 0xFF68, Usage 0x0061)
- Employs strict closed-loop Read-Back verification
- Saves before and after binary images for each step
- Detects exact changed byte offsets (target: +4..+5, 0x0191..0x0192 for Key A)
- Verifies that Actuation, RT Press, Flags, and all other 83 keys remain 100% untouched
- Requires explicit user confirmation before any physical hardware transmission
- Reverts hardware back to OFF (0.00 mm) upon experiment completion
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import sys
from typing import Dict, List, Optional, Tuple

from keyboard_re.models.base import (
    KEY_MAP,
    key_address,
    mm_to_units,
    resolve_key_name,
    units_to_mm,
)
from keyboard_re.models.state import HallProfileState
from keyboard_re.protocol.hall import (
    HALL_IMAGE_SIZE,
    build_hall_terminator,
    build_hall_write,
)
from keyboard_re.protocol.read import (
    read_device_info,
    read_hall_profile,
    read_keyboard_status,
)
from keyboard_re.protocol.transaction import TransactionStatus, write_transaction
from keyboard_re.transport.native_hid import NativeHidTransport


class RtReleaseExperimentStep:
    def __init__(
        self,
        target_mm: float,
        description: str,
    ):
        self.target_mm = target_mm
        self.description = description
        self.raw_uint16: int = mm_to_units(target_mm)
        self.before_bytes: Optional[bytes] = None
        self.after_bytes: Optional[bytes] = None
        self.changed_offsets: List[int] = []
        self.unexpected_offsets: List[int] = []
        self.read_back_mm: Optional[float] = None
        self.read_back_uint16: Optional[int] = None
        self.success: bool = False
        self.error: Optional[str] = None


def run_rt_release_research(
    key_name: str = "A",
    test_values: Optional[List[float]] = None,
    output_dir: str = "captures/research/rt_release",
    auto_confirm: bool = False,
    quiet: bool = False,
) -> Tuple[bool, List[RtReleaseExperimentStep]]:
    """
    Run controlled empirical RT Release discovery on a single physical key.
    """
    resolved_key = resolve_key_name(key_name)
    if not resolved_key or resolved_key not in KEY_MAP:
        raise ValueError(f"Unknown key '{key_name}'. Must be one of 84 keys in KEY_MAP.")

    bank, col = KEY_MAP[resolved_key]
    key_base_addr = key_address(bank, col)
    # Key record: 8 bytes
    # Bytes 0..1: Actuation  (addr..addr+1)
    # Bytes 2..3: RT Press   (addr+2..addr+3)
    # Bytes 4..5: RT Release (addr+4..addr+5)
    # Bytes 6..7: Flags      (addr+6..addr+7)
    rt_release_addr = key_base_addr + 4

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if test_values is None:
        # Default test progression: start at OFF, test safe increments, end with restoration to OFF
        test_values = [0.00, 0.10, 0.20, 0.50, 1.00, 0.00]

    steps: List[RtReleaseExperimentStep] = []
    for val in test_values:
        desc = "OFF (Rapid Trigger release disabled)" if val == 0.0 else f"RT Release = {val:.2f} mm"
        steps.append(RtReleaseExperimentStep(target_mm=val, description=desc))

    transport = NativeHidTransport()
    print(f"Connecting to {transport.product_name}...")
    try:
        transport.open()
    except Exception as e:
        print(f"Error opening HID transport: {e}", file=sys.stderr)
        return False, []

    try:
        info = read_device_info(transport)
        status = read_keyboard_status(transport)
        print(f"Connected: {transport.actual_product_name} (FW: v{info.firmware_version}, Active Profile: #{status.active_profile})")
        print(f"Target Key:      {resolved_key} (Bank {bank}, Col {col})")
        print(f"Record Address:  0x{key_base_addr:04X}")
        print(f"RT Release Addr: 0x{rt_release_addr:04X}..0x{rt_release_addr+1:04X} (uint16_le)")
        print(f"Total Steps:     {len(steps)}")

        for idx, step in enumerate(steps):
            print("\n" + "=" * 64)
            print(f"EXPERIMENT STEP {idx+1}/{len(steps)}: Key {resolved_key} -> {step.description}")
            print("=" * 64)

            # 1. Read live baseline
            print("Reading current live state from hardware...")
            live_img = read_hall_profile(transport, profile_id=1)
            step.before_bytes = live_img

            # Save step before.bin
            step_before_file = out_path / f"step_{idx:02d}_{step.target_mm:.2f}mm_before.bin"
            step_before_file.write_bytes(live_img)

            # Parse key record
            hall_state = HallProfileState.from_bytes(live_img, profile_id=1)
            cfg = hall_state.get_key(resolved_key)
            current_actuation = cfg.actuation_mm
            current_press_mm = cfg.rt_press_mm
            current_release_mm = cfg.rt_release_mm
            current_release_raw = cfg.rt_release
            current_flags = cfg.flags

            target_raw = step.raw_uint16
            old_bytes = struct.pack("<H", current_release_raw)
            new_bytes = struct.pack("<H", target_raw)

            print(f"Keyboard:            {transport.actual_product_name}")
            print(f"Key:                 {resolved_key}")
            print(f"Current RT Release:  {current_release_mm:.2f} mm (raw: 0x{current_release_raw:04X}, dec: {current_release_raw})")
            print(f"Target RT Release:   {step.target_mm:.2f} mm (raw: 0x{target_raw:04X}, dec: {target_raw})")
            print(f"Changed byte(s):     0x{rt_release_addr:04X}..0x{rt_release_addr+1:04X}: {old_bytes.hex(' ').upper()} -> {new_bytes.hex(' ').upper()}")
            print(f"Preserved fields:    Actuation={current_actuation:.2f}mm, RT_Press={current_press_mm:.2f}mm, Flags=0x{current_flags:04X}")
            print("=" * 64)

            # 2. User Confirmation
            if not auto_confirm:
                try:
                    ans = input(f"Execute Step {idx+1} on physical hardware? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\nABORTED BY USER")
                    step.error = "Aborted by user"
                    return False, steps

                if ans != "y":
                    print("ABORTED BY USER")
                    step.error = "Aborted by user"
                    return False, steps

            # 3. Construct modified 1008-byte image
            # Only modify rt_release! Leave actuation, rt_press, flags untouched.
            cfg.rt_release = target_raw
            hall_state.set_key(resolved_key, cfg)
            modified_image = hall_state.to_bytes()

            # Build official 19-report write burst
            packets = build_hall_write(modified_image) + [build_hall_terminator()]

            # 4. Transmit with ACK validation
            print(f"Dispatching 19 output reports to hardware...")
            tx_res = write_transaction(
                transport,
                packets,
                expected_ack_opcode=0x27,
                verbose=not quiet,
            )

            if tx_res.status != TransactionStatus.SUCCESS:
                err_msg = f"Write transaction failed: {tx_res.error} (sent: {tx_res.packets_sent}, acks: {tx_res.acks_received})"
                print(f"ERROR: {err_msg}", file=sys.stderr)
                step.error = err_msg
                return False, steps

            # 5. Read-back verification
            print("Performing hardware Read-Back verification (AA 17)...")
            read_back_img = read_hall_profile(transport, profile_id=1)
            step.after_bytes = read_back_img

            # Save step after.bin
            step_after_file = out_path / f"step_{idx:02d}_{step.target_mm:.2f}mm_after.bin"
            step_after_file.write_bytes(read_back_img)

            # 6. Analyze byte-level differences
            changed = []
            for off in range(HALL_IMAGE_SIZE):
                if live_img[off] != read_back_img[off]:
                    changed.append(off)

            step.changed_offsets = changed
            expected_offsets = {rt_release_addr, rt_release_addr + 1}
            step.unexpected_offsets = [off for off in changed if off not in expected_offsets]

            # Parse read-back key record
            rb_hall = HallProfileState.from_bytes(read_back_img, profile_id=1)
            rb_cfg = rb_hall.get_key(resolved_key)
            step.read_back_mm = rb_cfg.rt_release_mm
            step.read_back_uint16 = rb_cfg.rt_release

            # Verify integrity
            if step.unexpected_offsets:
                err_msg = f"Unexpected byte offsets modified: {[hex(o) for o in step.unexpected_offsets]}"
                print(f"WARNING: {err_msg}", file=sys.stderr)
                step.error = err_msg
                step.success = False
                return False, steps

            # Verify read-back matches target
            if step.read_back_uint16 != target_raw:
                err_msg = f"Read-back mismatch: expected raw {target_raw}, got {step.read_back_uint16}"
                print(f"ERROR: {err_msg}", file=sys.stderr)
                step.error = err_msg
                step.success = False
                return False, steps

            step.success = True
            print(f"STEP {idx+1} SUCCESS: RT Release = {step.read_back_mm:.2f} mm (raw uint16: 0x{step.read_back_uint16:04X})")
            print(f"Changed offsets: {[hex(o) for o in changed]} | Unexpected changes: NONE")

        return True, steps

    finally:
        transport.close()
        print("\nHID transport closed.")


def print_summary_table(steps: List[RtReleaseExperimentStep]) -> None:
    """
    Format and display research table: Value (mm) -> Raw uint16.
    """
    print("\n" + "=" * 78)
    print("RT RELEASE ENCODING EMPIRICAL TABLE (IO Type 84 Magnetic Black)")
    print("=" * 78)
    print(f"{'Target (mm)':<12} | {'Raw uint16 (HEX)':<18} | {'Raw uint16 (DEC)':<18} | {'Offsets':<10} | {'Status'}")
    print("-" * 12 + "-+-" + "-" * 18 + "-+-" + "-" * 18 + "-+-" + "-" * 10 + "-+-" + "-" * 8)

    for s in steps:
        if s.success:
            hex_str = f"0x{s.read_back_uint16:04X} ({s.read_back_uint16 & 0xFF:02X} {(s.read_back_uint16 >> 8) & 0xFF:02X})"
            dec_str = f"{s.read_back_uint16}"
            off_str = ", ".join(f"0x{o:04X}" for o in s.changed_offsets) if s.changed_offsets else "NONE (same)"
            status_str = "VERIFIED"
        else:
            hex_str = "N/A"
            dec_str = "N/A"
            off_str = "N/A"
            status_str = f"FAILED: {s.error}"

        print(f"{s.target_mm:<12.2f} | {hex_str:<18} | {dec_str:<18} | {off_str:<10} | {status_str}")

    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Empirical research runner for Rapid Trigger (RT) Release encoding."
    )
    parser.add_argument("--key", default="A", help="Physical key name to test (default: A)")
    parser.add_argument("--values", nargs="+", type=float, default=[0.00, 0.10, 0.20, 0.50, 1.00, 0.00], help="List of RT Release values in mm to test sequentially")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm all experiment prompts")
    parser.add_argument("--quiet", action="store_true", help="Suppress packet-level TX/RX dumps")
    parser.add_argument("-o", "--output-dir", default="captures/research/rt_release", help="Directory to save before/after images")

    args = parser.parse_args(argv)
    success, steps = run_rt_release_research(
        key_name=args.key,
        test_values=args.values,
        output_dir=args.output_dir,
        auto_confirm=args.yes,
        quiet=args.quiet,
    )

    print_summary_table(steps)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
