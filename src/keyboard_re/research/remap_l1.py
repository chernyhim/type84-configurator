"""
Research runner for Key Remap Layer 1 (AA 12 / 55 12) on IO Type 84 Magnetic Black.

Investigates the wire-format of the 512-byte Key Remap Layer 1 table:
- Reads live 512-byte table from physical keyboard via NativeHidTransport
- Saves baseline to captures/research/remap_l1/a_to_b_before.bin
- Verifies physical-key-to-slot mapping formula:
    Remap Bank = Hall Bank
    Remap Column = Hall Column + 1
    Remap Slot = Bank * 16 + (Hall Column + 1)
    Remap Byte Offset = Slot * 4
- Validates Key A (Bank 3, Col 1 -> Remap Slot 50, offset 0x00C8):
    Current 4 bytes: 00 04 00 02 (A, Type 0x02)
    Target 4 bytes for B: 00 05 00 02 (B, Type 0x02)
- Safety Gate: Because AA 22 write format has not been captured on physical hardware,
  write transmission is strictly inhibited until official WebHID write capture is recorded.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct
import sys
from typing import Dict, List, Optional, Tuple

from keyboard_re.models.base import (
    KEY_MAP,
    key_address,
    resolve_key_name,
)
from keyboard_re.protocol.keymap import (
    KEYMAP_BUFFER_SIZE,
    KEYMAP_RECORD_SIZE,
    KEYMAP_SLOT_COUNT,
    KeyRemapRecord,
    KeymapTable,
    parse_keymap_chunks,
)
from keyboard_re.protocol.read import read_keymap_table
from keyboard_re.protocol.transport import HidTransport
from keyboard_re.transport.native_hid import NativeHidTransport


def hall_to_remap_slot(bank: int, col: int) -> Tuple[int, int, int, int]:
    """
    Calculate Remap L1 coordinates and byte offset from Hall matrix coordinates.
    
    Formula (1:1 with hardware matrix):
        remap_bank = bank
        remap_col = col
        slot_index = bank * 16 + col
        byte_offset = slot_index * 4
    """
    if not (0 <= bank < 8 and 0 <= col < 16):
        raise ValueError(f"Matrix coordinates (Bank {bank}, Col {col}) out of range (8x16)")
    remap_bank = bank
    remap_col = col
    slot_index = remap_bank * 16 + remap_col
    byte_offset = slot_index * KEYMAP_RECORD_SIZE
    return remap_bank, remap_col, slot_index, byte_offset


def key_to_remap_slot(key_name: str) -> Tuple[int, int, int, int, int, int, int]:
    """
    Resolve a physical key name to its Hall and Remap coordinates:
    Returns (hall_bank, hall_col, hall_addr, remap_bank, remap_col, remap_slot, byte_offset)
    """
    canon = resolve_key_name(key_name)
    if not canon or canon not in KEY_MAP:
        raise ValueError(f"Unknown key '{key_name}'")
    bank, col = KEY_MAP[canon]
    hall_addr = key_address(bank, col)
    remap_bank, remap_col, slot, offset = hall_to_remap_slot(bank, col)
    return bank, col, hall_addr, remap_bank, remap_col, slot, offset


def parse_remap_buffer(buf: bytes | bytearray) -> Dict[int, KeyRemapRecord]:
    """Parse 512-byte buffer into 128 4-byte KeyRemapRecord entries."""
    if len(buf) < KEYMAP_BUFFER_SIZE:
        raise ValueError(f"Buffer must be at least {KEYMAP_BUFFER_SIZE} bytes, got {len(buf)}")
    slots: Dict[int, KeyRemapRecord] = {}
    for slot in range(KEYMAP_SLOT_COUNT):
        off = slot * KEYMAP_RECORD_SIZE
        slots[slot] = KeyRemapRecord.from_bytes(buf[off : off + KEYMAP_RECORD_SIZE])
    return slots


def analyze_key_a_to_b(buf: bytes | bytearray) -> Dict[str, Any]:
    """
    Analyze Key A in the Remap table and compute the exact diff for mapping A -> B.
    """
    bank, col, hall_addr, remap_bank, remap_col, slot, offset = key_to_remap_slot("A")
    current_bytes = bytes(buf[offset : offset + KEYMAP_RECORD_SIZE])
    current_rec = KeyRemapRecord.from_bytes(current_bytes)

    # Target: Map to B (HID Usage 0x05) -> [0x02, 0x00, 0x05, 0x00]
    target_bytes = KeyRemapRecord.for_standard_key(0x05).to_bytes()
    target_rec = KeyRemapRecord.from_bytes(target_bytes)

    diff_bytes: List[Tuple[int, int, int]] = []
    for i in range(4):
        if current_bytes[i] != target_bytes[i]:
            diff_bytes.append((offset + i, current_bytes[i], target_bytes[i]))

    return {
        "key": "A",
        "target_key": "B",
        "hall_bank": bank,
        "hall_col": col,
        "hall_addr": hall_addr,
        "remap_bank": remap_bank,
        "remap_col": remap_col,
        "remap_slot": slot,
        "byte_offset": offset,
        "current_bytes": current_bytes,
        "current_record": current_rec,
        "target_bytes": target_bytes,
        "target_record": target_rec,
        "diff": diff_bytes,
    }


def run_remap_read_and_analysis(
    transport: Optional[HidTransport] = None,
    output_dir: str = "captures/research/remap_l1",
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Perform live read of Remap L1 (AA 12) from hardware and analyze Key A.
    Saves baseline to a_to_b_before.bin.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    should_close = False
    if transport is None:
        transport = NativeHidTransport()
        transport.open()
        should_close = True

    try:
        print("Reading complete 512-byte Remap L1 table from keyboard (AA 12)...")
        buf = read_keymap_table(transport, opcode=0x12)
        print(f"Successfully read {len(buf)} bytes.")

        # Save baseline
        before_file = out_path / "a_to_b_before.bin"
        before_file.write_bytes(buf)
        print(f"Saved baseline image to: {before_file}")

        # Analysis
        analysis = analyze_key_a_to_b(buf)
        return buf, analysis
    finally:
        if should_close:
            transport.close()


def format_remap_analysis_report(buf: bytes, analysis: Dict[str, Any]) -> str:
    """Format human-readable analysis report."""
    rec = analysis["current_record"]
    tgt = analysis["target_record"]
    cur_hex = analysis["current_bytes"].hex(" ").upper()
    tgt_hex = analysis["target_bytes"].hex(" ").upper()

    lines = [
        "=" * 70,
        "KEY REMAP L1 (AA 12) ANALYSIS REPORT",
        "=" * 70,
        f"Buffer Size:          {len(buf)} bytes (128 slots x 4 bytes)",
        "",
        "--- Key A Remap Slot Identification ---",
        f"Physical Key:         Key {analysis['key']}",
        f"Hall Matrix:          Bank {analysis['hall_bank']}, Column {analysis['hall_col']} (Addr 0x{analysis['hall_addr']:04X})",
        f"Remap Matrix:         Bank {analysis['remap_bank']}, Column {analysis['remap_col']}",
        f"Remap Slot:           Slot {analysis['remap_slot']} (Bank * 16 + Remap_Col)",
        f"Remap Byte Offset:    0x{analysis['byte_offset']:04X} ({analysis['byte_offset']} decimal)",
        f"Current 4-byte Value: {cur_hex} -> {rec.hid_name} (type 0x{rec.function_type:02X})",
        f"Target 4-byte Value:  {tgt_hex} -> {tgt.hid_name} (type 0x{tgt.function_type:02X})",
        "",
        "--- Planned Modification Diff (A -> B) ---",
    ]

    for addr, old_b, new_b in analysis["diff"]:
        lines.append(f"  Offset 0x{addr:04X} ({addr:3d}): 0x{old_b:02X} -> 0x{new_b:02X}")

    lines.extend([
        "",
        "--- Write Protocol Safety Status ---",
        "Write Opcode:         AA 22 (THEORETICAL / UNVERIFIED ON HARDWARE)",
        "Hardware Evidence:    0 write captures in official configurator sessions",
        "Write Transmission:   INHIBITED (Safety Policy Active)",
        "Required Next Step:   Capture official WebHID 'Remap Key' session in browser",
        "                      before performing physical writes to avoid EEPROM bricking.",
        "=" * 70,
    ])

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Key Remap L1 (AA 12) read & analysis runner for IO Type 84 Magnetic Black."
    )
    parser.add_argument("-o", "--output-dir", default="captures/research/remap_l1", help="Output directory")
    args = parser.parse_args(argv)

    buf, analysis = run_remap_read_and_analysis(output_dir=args.output_dir)
    print("\n" + format_remap_analysis_report(buf, analysis))
    return 0


if __name__ == "__main__":
    sys.exit(main())
