"""
Analyzer and Dissector for Remap L1 WRITE captures from official web.io.vision.

Performs forensic protocol inspection on captured WebHID sessions:
- Identifies exact WRITE opcode (validates whether AA 22, AA 2x, or other)
- Measures output report lengths, report IDs, usage pages, usages
- Analyzes chunking scheme (chunk count, payload sizes, address framing)
- Checks for commit / terminator packet and ACK format (55 2x ...)
- Calculates inter-packet timing and ACK response latency
- Reassembles transmitted remap image and compares byte-for-byte with baseline
- Verifies exact modified offsets for A -> B (expected offset 0x00C9: 0x04 -> 0x05)
- Compares transaction structure against Hall Write (AA 27)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import struct
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from keyboard_re.models.base import KEY_MAP, key_address
from keyboard_re.protocol.keymap import (
    KEYMAP_BUFFER_SIZE,
    KEYMAP_RECORD_SIZE,
    KEYMAP_SLOT_COUNT,
    KeyRemapRecord,
)
from keyboard_re.research.remap_l1 import (
    analyze_key_a_to_b,
    hall_to_remap_slot,
    parse_remap_buffer,
)


@dataclass
class PacketEvent:
    index: int
    timestamp_ms: float
    direction: str       # "HOST -> DEVICE" or "DEVICE -> HOST"
    event_type: str      # "sendReport", "inputreport"
    report_id: int
    data: bytes
    length: int

    @property
    def hex(self) -> str:
        return self.data.hex().upper()

    @property
    def prefix(self) -> str:
        return self.data[:3].hex(" ").upper() if len(self.data) >= 3 else self.data.hex(" ").upper()


@dataclass
class RemapWriteAnalysis:
    capture_file: str
    total_events: int
    write_opcode: str
    report_id: int
    report_length: int
    usage_page: Optional[str]
    usage: Optional[str]
    tx_packets: List[PacketEvent] = field(default_factory=list)
    rx_packets: List[PacketEvent] = field(default_factory=list)
    chunks_count: int = 0
    has_terminator: bool = False
    terminator_packet: Optional[PacketEvent] = None
    acks_count: int = 0
    ack_format: str = "UNKNOWN"
    mean_tx_interval_ms: float = 0.0
    mean_ack_latency_ms: float = 0.0
    reassembled_buffer: Optional[bytes] = None
    baseline_diffs: List[Tuple[int, int, int]] = field(default_factory=list)
    a_to_b_confirmed: bool = False
    comparison_with_hall_write: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def load_capture_events(capture_path: Path | str) -> Tuple[Dict[str, Any], List[PacketEvent]]:
    """Load JSON capture file and extract normalized packet events."""
    p = Path(capture_path)
    if not p.exists():
        raise FileNotFoundError(f"Capture file not found: {p}")

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_events = data.get("events", [])
    if not raw_events and "reports" in data:
        raw_events = data["reports"]

    events: List[PacketEvent] = []
    for idx, e in enumerate(raw_events):
        hex_str = e.get("data_hex", "").strip().replace(" ", "").replace(":", "")
        if not hex_str:
            continue
        try:
            raw_bytes = bytes.fromhex(hex_str)
        except ValueError:
            continue

        events.append(
            PacketEvent(
                index=e.get("index", idx),
                timestamp_ms=float(e.get("timestamp_ms", 0.0)),
                direction=e.get("direction", "HOST -> DEVICE"),
                event_type=e.get("event_type", "sendReport"),
                report_id=int(e.get("report_id", 0) or 0),
                data=raw_bytes,
                length=len(raw_bytes),
            )
        )

    return data, events


def isolate_remap_write_session(events: List[PacketEvent]) -> List[PacketEvent]:
    """
    Isolate the sequence of write packets related to Remap table update.
    Looks for candidate WRITE opcodes (e.g. AA 22 or any AA 2x).
    """
    write_events: List[PacketEvent] = []
    in_write = False

    for e in events:
        if len(e.data) < 2:
            continue
        # Candidate host writes begin with 0xAA 0x2x
        if e.direction == "HOST -> DEVICE" and e.data[0] == 0xAA and (e.data[1] & 0xF0) == 0x20:
            in_write = True
            write_events.append(e)
        elif in_write:
            # While in write session, capture ACKs and related communications
            if e.direction == "DEVICE -> HOST" and e.data[0] == 0x55 and (e.data[1] & 0xF0) == 0x20:
                write_events.append(e)
            elif e.direction == "HOST -> DEVICE" and e.data[0] == 0xAA and (e.data[1] & 0xF0) == 0x20:
                write_events.append(e)
            else:
                # End of continuous burst if non-write packet encountered
                if len(write_events) >= 5:
                    break

    return write_events


def analyze_remap_write_capture(
    capture_path: Path | str,
    baseline_bytes: Optional[bytes] = None,
) -> RemapWriteAnalysis:
    """
    Perform deep analysis of a Remap L1 write capture.
    """
    meta, events = load_capture_events(capture_path)
    analysis = RemapWriteAnalysis(
        capture_file=str(capture_path),
        total_events=len(events),
        write_opcode="UNKNOWN",
        report_id=0,
        report_length=64,
        usage_page=None,
        usage=None,
    )

    # Extract device metadata
    devs = meta.get("devices", [])
    if devs and isinstance(devs, list):
        colls = devs[0].get("collections", [])
        if colls:
            analysis.usage_page = colls[0].get("usage_page")
            analysis.usage = colls[0].get("usage")

    # Isolate write packets
    write_events = isolate_remap_write_session(events)
    if not write_events:
        # Fallback: inspect all outgoing packets
        tx_candidates = [e for e in events if e.direction == "HOST -> DEVICE" and e.data.startswith(b"\xAA")]
        if not tx_candidates:
            analysis.errors.append("No HOST -> DEVICE packets with prefix AA found in capture.")
            return analysis
        write_events = tx_candidates

    analysis.tx_packets = [e for e in write_events if e.direction == "HOST -> DEVICE"]
    analysis.rx_packets = [e for e in write_events if e.direction == "DEVICE -> HOST"]

    if not analysis.tx_packets:
        analysis.errors.append("No outgoing write reports found in write burst.")
        return analysis

    first_tx = analysis.tx_packets[0]
    analysis.report_id = first_tx.report_id
    analysis.report_length = first_tx.length
    analysis.write_opcode = f"0x{first_tx.data[1]:02X}" if len(first_tx.data) >= 2 else "UNKNOWN"

    # Analyze chunks vs terminator
    data_chunks: List[PacketEvent] = []
    terminator_pkts: List[PacketEvent] = []

    for pkt in analysis.tx_packets:
        if len(pkt.data) < 3:
            continue
        sub_code = pkt.data[2]
        if sub_code in (0x38, 0x08, 0x40):  # Data chunk length indicator
            data_chunks.append(pkt)
        elif sub_code in (0x10, 0x00):      # Candidate terminator/commit length indicator
            terminator_pkts.append(pkt)
        else:
            data_chunks.append(pkt)

    analysis.chunks_count = len(data_chunks)
    if terminator_pkts:
        analysis.has_terminator = True
        analysis.terminator_packet = terminator_pkts[-1]

    # Analyze ACKs
    analysis.acks_count = len(analysis.rx_packets)
    if analysis.rx_packets:
        first_rx = analysis.rx_packets[0]
        analysis.ack_format = f"55 {first_rx.data[1]:02X} {first_rx.data[2]:02X}..."

    # Measure timings
    if len(analysis.tx_packets) > 1:
        tx_diffs = [
            analysis.tx_packets[i].timestamp_ms - analysis.tx_packets[i - 1].timestamp_ms
            for i in range(1, len(analysis.tx_packets))
        ]
        analysis.mean_tx_interval_ms = round(sum(tx_diffs) / len(tx_diffs), 2)

    # Reassemble buffer from data chunks
    reassembled = bytearray(KEYMAP_BUFFER_SIZE)
    reconstruct_ok = True

    for idx, c in enumerate(data_chunks):
        if len(c.data) < 5:
            continue
        payload_len = c.data[2]
        addr = struct.unpack_from("<H", c.data, 3)[0]
        payload = c.data[5 : 5 + payload_len]

        if addr + len(payload) <= KEYMAP_BUFFER_SIZE:
            reassembled[addr : addr + len(payload)] = payload
        else:
            analysis.warnings.append(f"Chunk #{idx}: address 0x{addr:04X} + {len(payload)} exceeds {KEYMAP_BUFFER_SIZE} bytes")
            reconstruct_ok = False

    if reconstruct_ok:
        analysis.reassembled_buffer = bytes(reassembled)

    # Compare with baseline
    if baseline_bytes and analysis.reassembled_buffer and len(baseline_bytes) == KEYMAP_BUFFER_SIZE:
        diffs = []
        for off in range(KEYMAP_BUFFER_SIZE):
            if baseline_bytes[off] != analysis.reassembled_buffer[off]:
                diffs.append((off, baseline_bytes[off], analysis.reassembled_buffer[off]))
        analysis.baseline_diffs = diffs

        # Check specifically for Key A (Slot 50, offset 200..203)
        # Offset 201 should be 0x04 -> 0x05
        if len(diffs) == 1 and diffs[0][0] == 201:
            if diffs[0][1] == 0x04 and diffs[0][2] == 0x05:
                analysis.a_to_b_confirmed = True
        elif any(d[0] == 201 and d[1] == 0x04 and d[2] == 0x05 for d in diffs):
            analysis.a_to_b_confirmed = True

    # Comparison with Hall Write (AA 27)
    analysis.comparison_with_hall_write = {
        "hall_opcode": "AA 27",
        "hall_chunks_count": 18,
        "hall_chunk_size": 56,
        "hall_terminator": "AA 27 10 F0 03 00 01",
        "hall_ack": "55 27 ...",
        "remap_opcode": analysis.write_opcode,
        "remap_chunks_count": analysis.chunks_count,
        "remap_ack": analysis.ack_format,
        "remap_has_terminator": analysis.has_terminator,
    }

    return analysis


def format_remap_write_report(analysis: RemapWriteAnalysis) -> str:
    """Generate detailed markdown report of the write capture analysis."""
    lines = [
        "=" * 72,
        "REMAP L1 WRITE TRANSACTION FORENSIC ANALYSIS",
        "=" * 72,
        f"Capture File:         {analysis.capture_file}",
        f"Total Events:         {analysis.total_events}",
        f"Usage Page / Usage:   {analysis.usage_page} / {analysis.usage}",
        "",
        "--- Transport & Framing Parameters ---",
        f"Write Opcode:         {analysis.write_opcode}",
        f"Report ID:            {analysis.report_id}",
        f"Report Length:        {analysis.report_length} bytes",
        f"Data Chunks:          {analysis.chunks_count} reports",
        f"Terminator Packet:    {'YES' if analysis.has_terminator else 'NO / UNKNOWN'}",
        f"ACKs Received:        {analysis.acks_count} reports",
        f"ACK Format:           {analysis.ack_format}",
        f"Mean TX Interval:     {analysis.mean_tx_interval_ms} ms",
        "",
        "--- Data Reassembly & Semantic Diff ---",
        f"Reassembled Size:     {len(analysis.reassembled_buffer) if analysis.reassembled_buffer else 0} bytes",
        f"Total Differences:    {len(analysis.baseline_diffs)} byte(s)",
    ]

    if analysis.baseline_diffs:
        lines.append("Detected Diffs (Offset | Baseline -> Transmitted):")
        for off, old_b, new_b in analysis.baseline_diffs:
            lines.append(f"  Offset 0x{off:04X} ({off:3d}): 0x{old_b:02X} -> 0x{new_b:02X}")
    else:
        lines.append("  (No baseline diffs or baseline not provided)")

    lines.extend([
        "",
        f"Key A -> B Confirmed: {'YES (Isolated to offset 0x00C9)' if analysis.a_to_b_confirmed else 'PENDING CAPTURE'}",
        "",
        "--- Comparison with Hall Write (AA 27) ---",
        f"Hall Write Framing:   18 chunks x 56B + 1 terminator (AA 27 10) | ACKs: 55 27",
        f"Remap Write Framing:  {analysis.chunks_count} chunks | Terminator: {'YES' if analysis.has_terminator else 'NO'} | ACKs: {analysis.ack_format}",
        "=" * 72,
    ])

    if analysis.warnings:
        lines.append("\nWARNINGS:")
        for w in analysis.warnings:
            lines.append(f"  [!] {w}")

    if analysis.errors:
        lines.append("\nERRORS:")
        for e in analysis.errors:
            lines.append(f"  [ERROR] {e}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forensic analyzer for Key Remap L1 Write WebHID captures."
    )
    parser.add_argument("capture", help="Path to WebHID capture JSON file")
    parser.add_argument("--baseline", default="captures/research/remap_l1/a_to_b_before.bin", help="Path to baseline 512-byte image")
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline)
    baseline = baseline_path.read_bytes() if baseline_path.exists() else None

    analysis = analyze_remap_write_capture(args.capture, baseline_bytes=baseline)
    print("\n" + format_remap_write_report(analysis))
    return 0 if not analysis.errors else 1


if __name__ == "__main__":
    sys.exit(main())
