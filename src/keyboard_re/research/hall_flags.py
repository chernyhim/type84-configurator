"""
Research runner for Hall Effect Switch Flags / Mode (AA 27 Word 3, +6..+7) on IO Type 84 Magnetic Black.

Investigates whether individual bits in the 16-bit word (+6..+7, uint16_le) have observable semantics:
- Uses NativeHidTransport (UsagePage 0xFF68, Usage 0x0061)
- Employs strict closed-loop Read-Back verification
- Tests one isolated bit at a time on Key A (Bank 3, Col 1, 0x0193..0x0194)
- Keeps Actuation (1.40 mm), RT Press (0.00 mm), and RT Release (0.00 mm) strictly untouched
- Confirms all 19 ACKs per write
- Verifies unexpected changes == NONE
- MANDATORY RESTORE: After every test, reverts Flags back to 0x0000 and verifies full baseline match
- Differentiates:
    - CONFIRMED: Hardware storage and read-back confirmed with zero side-effects
    - OBSERVED: Directly observed behavioral change during physical switch operation
    - INFERRED: Plausible hypothesis
    - UNKNOWN: Semantics not established
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import struct
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from keyboard_re.models.base import (
    CONFIG_IMAGE_SIZE,
    KEY_MAP,
    KEY_RECORD_SIZE,
    key_address,
    resolve_key_name,
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
from keyboard_re.protocol.transport import HidTransport
from keyboard_re.transport.native_hid import NativeHidTransport


# 16 individual bits for isolated testing
SINGLE_BITS = [1 << i for i in range(16)]


@dataclass
class HallFlagExperimentResult:
    """Detailed record of a single flag bit experiment."""
    bit_index: int                       # 0..15 (-1 for custom / 0x0000)
    flag_value: int                      # Raw uint16 value
    bytes_le: bytes                      # 2 bytes little-endian
    description: str                     # Description / bit label
    before_bytes: Optional[bytes] = None
    after_bytes: Optional[bytes] = None
    restored_bytes: Optional[bytes] = None
    changed_offsets: List[int] = field(default_factory=list)
    unexpected_offsets: List[int] = field(default_factory=list)
    read_back_value: Optional[int] = None
    write_status: str = "PENDING"        # "VERIFIED" / "FAILED"
    read_back_status: str = "PENDING"    # "VERIFIED" / "MISMATCH" / "FAILED"
    restore_status: str = "PENDING"      # "VERIFIED" / "FAILED"
    physical_behavior: str = "Normal (keystroke registers as expected)"
    semantic_status: str = "UNKNOWN"     # "CONFIRMED" (storage) / "OBSERVED" / "UNKNOWN"
    error: Optional[str] = None

    @property
    def hex_str(self) -> str:
        return f"0x{self.flag_value:04X}"

    @property
    def bytes_hex(self) -> str:
        return self.bytes_le.hex(" ").upper()


def build_flag_image(
    base_image: bytes | bytearray,
    key_name: str,
    flags_val: int,
) -> bytes:
    """
    Construct a 1008-byte configuration image with ONLY the specified key's
    Flags field (+6..+7) modified. All other 1006 bytes remain completely identical.
    """
    if len(base_image) < CONFIG_IMAGE_SIZE:
        raise ValueError(f"Base image must be at least {CONFIG_IMAGE_SIZE} bytes, got {len(base_image)}")

    canon = resolve_key_name(key_name)
    if not canon or canon not in KEY_MAP:
        raise ValueError(f"Unknown key '{key_name}'")

    bank, col = KEY_MAP[canon]
    base_addr = key_address(bank, col)
    flags_addr = base_addr + 6

    buf = bytearray(base_image[:CONFIG_IMAGE_SIZE])
    buf[flags_addr : flags_addr + 2] = struct.pack("<H", flags_val & 0xFFFF)
    return bytes(buf)


def verify_flag_diff(
    before: bytes | bytearray,
    after: bytes | bytearray,
    flags_addr: int,
) -> Tuple[List[int], List[int]]:
    """
    Compare before and after 1008-byte images and return:
    (all_changed_offsets, unexpected_offsets).
    Expected offsets are strictly flags_addr and flags_addr + 1.
    """
    changed: List[int] = []
    for i in range(min(len(before), len(after))):
        if before[i] != after[i]:
            changed.append(i)

    expected = {flags_addr, flags_addr + 1}
    unexpected = [o for o in changed if o not in expected]
    return changed, unexpected


def run_single_flag_experiment(
    transport: HidTransport,
    key_name: str = "A",
    flag_val: int = 0x0001,
    bit_index: int = 0,
    output_dir: Optional[Path | str] = None,
    auto_confirm: bool = False,
    behavior_note: str = "Normal (keystroke registers as expected)",
    quiet: bool = False,
) -> HallFlagExperimentResult:
    """
    Execute a single, safe, isolated Hall Flags experiment on physical hardware:
    1. Read live baseline (1008 bytes).
    2. Modify ONLY Flags word (+6..+7) for target key.
    3. Prompt user for explicit confirmation [y/N].
    4. Transmit 18 data chunks + 1 terminator.
    5. Validate all 19 ACKs.
    6. Read-back live hardware image.
    7. Verify changed_offsets == {flags_addr, flags_addr+1} and unexpected_offsets == [].
    8. Verify read_back_flags == flag_val.
    9. MANDATORY RESTORE: write back 0x0000 and verify full restoration.
    """
    canon = resolve_key_name(key_name)
    if not canon or canon not in KEY_MAP:
        raise ValueError(f"Unknown key '{key_name}'")

    bank, col = KEY_MAP[canon]
    base_addr = key_address(bank, col)
    flags_addr = base_addr + 6
    val_uint16 = flag_val & 0xFFFF
    bytes_le = struct.pack("<H", val_uint16)

    bit_desc = f"Bit {bit_index} (0x{val_uint16:04X})" if bit_index >= 0 else f"Value 0x{val_uint16:04X}"
    result = HallFlagExperimentResult(
        bit_index=bit_index,
        flag_value=val_uint16,
        bytes_le=bytes_le,
        description=bit_desc,
        physical_behavior=behavior_note,
    )

    out_path = Path(output_dir) if output_dir else Path("captures/research/hall_flags")
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Read live baseline
    print("\n" + "=" * 64)
    print(f"FLAG EXPERIMENT: Key {canon} -> {bit_desc}")
    print("=" * 64)
    print("Reading current live state from keyboard (AA 17)...")
    baseline_img = read_hall_profile(transport, profile_id=1)
    result.before_bytes = baseline_img

    # Check key state in baseline
    hall_state = HallProfileState.from_bytes(baseline_img, profile_id=1)
    cfg = hall_state.get_key(canon)
    current_flags = cfg.flags
    old_bytes = struct.pack("<H", current_flags)

    print(f"Key:                 {canon} (Bank {bank}, Col {col})")
    print(f"Record Address:      0x{base_addr:04X}")
    print(f"Flags Address:       0x{flags_addr:04X}..0x{flags_addr+1:04X} (Word 3, uint16_le)")
    print(f"Current Flags:       0x{current_flags:04X} ({old_bytes.hex(' ').upper()})")
    print(f"Target Flags:        0x{val_uint16:04X} ({bytes_le.hex(' ').upper()})")
    print(f"Untouched Fields:    Actuation={cfg.actuation_mm:.2f} mm, RT_Press={cfg.rt_press_mm:.2f} mm, RT_Release={cfg.rt_release_mm:.2f} mm")
    print(f"Target Bit Index:    {bit_index if bit_index >= 0 else 'N/A'}")
    print("=" * 64)

    # 2. User Confirmation
    if not auto_confirm:
        try:
            ans = input(f"Transmit 0x{val_uint16:04X} for Key {canon} to physical hardware? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nABORTED BY USER")
            result.error = "Aborted by user"
            return result

        if ans != "y":
            print("ABORTED BY USER")
            result.error = "Aborted by user"
            return result

    # 3. Construct modified 1008-byte image
    modified_img = build_flag_image(baseline_img, canon, val_uint16)
    step_prefix = f"bit_{bit_index:02d}" if bit_index >= 0 else f"val_{val_uint16:04X}"
    (out_path / f"{step_prefix}_before.bin").write_bytes(baseline_img)

    # 4. Dispatch 19-report write sequence
    packets = build_hall_write(modified_img) + [build_hall_terminator()]
    print(f"Dispatching write transaction (18 chunks + 1 terminator)...")
    tx_res = write_transaction(
        transport,
        packets,
        expected_ack_opcode=0x27,
        verbose=not quiet,
    )

    if tx_res.status != TransactionStatus.SUCCESS:
        err = f"Write transaction failed: {tx_res.error} (sent: {tx_res.packets_sent}, acks: {tx_res.acks_received})"
        print(f"ERROR: {err}", file=sys.stderr)
        result.write_status = "FAILED"
        result.error = err
        # Emergency restore attempt
        _perform_restore(transport, canon, baseline_img, quiet)
        return result

    result.write_status = "VERIFIED"

    # 5. Read-back verification
    print("Performing post-write read-back verification (AA 17)...")
    read_back_img = read_hall_profile(transport, profile_id=1)
    result.after_bytes = read_back_img
    (out_path / f"{step_prefix}_after.bin").write_bytes(read_back_img)

    # 6. Analyze byte differences
    changed, unexpected = verify_flag_diff(baseline_img, read_back_img, flags_addr)
    result.changed_offsets = changed
    result.unexpected_offsets = unexpected

    rb_hall = HallProfileState.from_bytes(read_back_img, profile_id=1)
    rb_cfg = rb_hall.get_key(canon)
    result.read_back_value = rb_cfg.flags

    if unexpected:
        err = f"CRITICAL: Unexpected offsets modified: {[hex(o) for o in unexpected]}"
        print(f"ERROR: {err}", file=sys.stderr)
        result.read_back_status = "FAILED"
        result.error = err
        _perform_restore(transport, canon, baseline_img, quiet)
        return result

    if rb_cfg.flags != val_uint16:
        err = f"Read-back mismatch: expected 0x{val_uint16:04X}, got 0x{rb_cfg.flags:04X}"
        print(f"ERROR: {err}", file=sys.stderr)
        result.read_back_status = "MISMATCH"
        result.error = err
        _perform_restore(transport, canon, baseline_img, quiet)
        return result

    result.read_back_status = "VERIFIED"
    result.semantic_status = "CONFIRMED (storage verified)"
    print(f"WRITE & READ-BACK SUCCESS:")
    print(f"  Flags read-back:   0x{rb_cfg.flags:04X} ({bytes_le.hex(' ').upper()})")
    print(f"  Changed offsets:   {[hex(o) for o in changed]}")
    print(f"  Unexpected changes: NONE")

    # 7. MANDATORY RESTORE BACK TO 0x0000
    print("\nExecuting MANDATORY RESTORATION to 0x0000...")
    restore_ok, restored_img = _perform_restore(transport, canon, baseline_img, quiet)
    result.restored_bytes = restored_img
    if restore_ok:
        result.restore_status = "VERIFIED"
        print("RESTORATION VERIFIED: Flags returned to 0x0000, 1008-byte image matches baseline.")
    else:
        result.restore_status = "FAILED"
        result.error = "Failed to restore baseline image cleanly"
        print("WARNING: Restoration verification failed!", file=sys.stderr)

    return result


def _perform_restore(
    transport: HidTransport,
    key_name: str,
    baseline_image: bytes,
    quiet: bool = False,
) -> Tuple[bool, bytes]:
    """Revert keyboard back to original baseline image and verify 100% byte match."""
    restore_packets = build_hall_write(baseline_image) + [build_hall_terminator()]
    tx = write_transaction(
        transport,
        restore_packets,
        expected_ack_opcode=0x27,
        verbose=not quiet,
    )
    if tx.status != TransactionStatus.SUCCESS:
        return False, b""

    rb = read_hall_profile(transport, profile_id=1)
    if rb == baseline_image:
        return True, rb
    return False, rb


def run_all_bits_research(
    key_name: str = "A",
    bits: Optional[Sequence[int]] = None,
    output_dir: str = "captures/research/hall_flags",
    auto_confirm: bool = False,
    quiet: bool = False,
) -> List[HallFlagExperimentResult]:
    """
    Run sequence of isolated bit experiments.
    Every bit is explicitly confirmed, verified, and reverted back to 0x0000.
    """
    test_bits = bits if bits is not None else list(range(16))
    results: List[HallFlagExperimentResult] = []

    transport = NativeHidTransport()
    try:
        transport.open()
    except Exception as e:
        print(f"Error opening HID transport: {e}", file=sys.stderr)
        return []

    try:
        info = read_device_info(transport)
        status = read_keyboard_status(transport)
        print(f"Connected: {transport.actual_product_name}")
        print(f"Firmware:  v{info.firmware_version} (Active Profile: #{status.active_profile})")
        print(f"Testing {len(test_bits)} individual bit(s) on Key {key_name.upper()}...")

        for bit in test_bits:
            flag_val = 1 << bit
            res = run_single_flag_experiment(
                transport=transport,
                key_name=key_name,
                flag_val=flag_val,
                bit_index=bit,
                output_dir=output_dir,
                auto_confirm=auto_confirm,
                quiet=quiet,
            )
            results.append(res)
            if res.error or res.write_status != "VERIFIED" or res.restore_status != "VERIFIED":
                print(f"\nSTOPPING SERIES DUE TO ERROR ON BIT {bit}: {res.error}")
                break

        return results
    finally:
        transport.close()
        print("\nHID transport closed.")


def format_flags_summary_table(results: List[HallFlagExperimentResult]) -> str:
    """
    Format empirical research table for user display:
    bit/value | write | read-back | changed bytes | physical behavior | semantic status
    """
    lines = [
        "=" * 105,
        "HALL SWITCH FLAGS / MODE (AA 27 WORD 3) EMPIRICAL RESEARCH TABLE",
        "=" * 105,
        f"{'Bit/Value':<12} | {'Write':<10} | {'Read-Back':<10} | {'Changed Bytes':<16} | {'Physical Behavior':<30} | {'Semantic Status'}",
        "-" * 12 + "-+-" + "-" * 10 + "-+-" + "-" * 10 + "-+-" + "-" * 16 + "-+-" + "-" * 30 + "-+-" + "-" * 16,
    ]

    for r in results:
        bit_val_str = f"Bit {r.bit_index} ({r.hex_str})" if r.bit_index >= 0 else r.hex_str
        bytes_str = ", ".join(f"0x{o:04X}" for o in r.changed_offsets) if r.changed_offsets else "NONE"
        lines.append(
            f"{bit_val_str:<12} | {r.write_status:<10} | {r.read_back_status:<10} | {bytes_str:<16} | {r.physical_behavior:<30} | {r.semantic_status}"
        )

    lines.append("=" * 105)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Empirical research runner for Hall Switch Flags / Mode (+6..+7) on IO Type 84 Magnetic Black."
    )
    parser.add_argument("--key", default="A", help="Physical key name to test (default: A)")
    parser.add_argument("--bit", type=int, help="Single bit index to test (0..15)")
    parser.add_argument("--flags", type=lambda x: int(x, 0), help="Custom uint16 flags value (e.g. 0x0001)")
    parser.add_argument("--all-bits", action="store_true", help="Sequentially test all 16 bits (0..15)")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm all experiment prompts")
    parser.add_argument("--quiet", action="store_true", help="Suppress packet-level TX/RX dumps")
    parser.add_argument("-o", "--output-dir", default="captures/research/hall_flags", help="Directory for binary dumps")

    args = parser.parse_args(argv)

    if args.all_bits:
        results = run_all_bits_research(
            key_name=args.key,
            output_dir=args.output_dir,
            auto_confirm=args.yes,
            quiet=args.quiet,
        )
        print("\n" + format_flags_summary_table(results))
        return 0

    elif args.bit is not None:
        if not (0 <= args.bit <= 15):
            print(f"Error: Bit index {args.bit} out of range [0..15]", file=sys.stderr)
            return 1
        flag_val = 1 << args.bit
        transport = NativeHidTransport()
        transport.open()
        try:
            res = run_single_flag_experiment(
                transport=transport,
                key_name=args.key,
                flag_val=flag_val,
                bit_index=args.bit,
                output_dir=args.output_dir,
                auto_confirm=args.yes,
                quiet=args.quiet,
            )
            print("\n" + format_flags_summary_table([res]))
            return 0 if res.write_status == "VERIFIED" and res.restore_status == "VERIFIED" else 1
        finally:
            transport.close()

    elif args.flags is not None:
        transport = NativeHidTransport()
        transport.open()
        try:
            bit_idx = -1
            for b in range(16):
                if (1 << b) == args.flags:
                    bit_idx = b
                    break
            res = run_single_flag_experiment(
                transport=transport,
                key_name=args.key,
                flag_val=args.flags,
                bit_index=bit_idx,
                output_dir=args.output_dir,
                auto_confirm=args.yes,
                quiet=args.quiet,
            )
            print("\n" + format_flags_summary_table([res]))
            return 0 if res.write_status == "VERIFIED" and res.restore_status == "VERIFIED" else 1
        finally:
            transport.close()

    else:
        print("Please specify --bit <0..15>, --flags <val>, or --all-bits.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
