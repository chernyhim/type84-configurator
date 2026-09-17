"""
RGB Protocol Analyzer for IO by Red Square Type 84 Magnetic Black.

Analyzes passive WebHID captures to isolate and decode:
- HID interface / usage page / usage
- Report ID and report length
- Invariant headers and opcodes
- Variable parameter bytes (color, brightness, effect, speed, per-key)
- Checksum / terminator / commit packets
- Command separation from key config (AA 27 ...)
- LED index correlation with physical layout vs electrical matrix

Strict safety guarantee: Passive analysis only. No hardware packets are transmitted.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from keyboard_re.models import (
    KEY_MAP,
    RawCapture,
    RawReport,
)


@dataclass
class HIDInterfaceInfo:
    """HID device interface and collection metadata."""
    vendor_id: str
    product_id: str
    product_name: str
    usage_page: Optional[str] = None
    usage: Optional[str] = None
    collections: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ReportSummary:
    """Summary of a single captured HID report."""
    index: int
    report_type: str  # "output" or "feature"
    report_id: int
    length: int
    data_hex: str
    usage_page: Optional[str] = None
    usage: Optional[str] = None
    opcode_hex: str = ""
    is_aa27: bool = False

    @property
    def data_bytes(self) -> bytes:
        return bytes.fromhex(self.data_hex)


@dataclass
class ChecksumCandidate:
    """Potential checksum detected at a specific byte offset."""
    algorithm: str  # e.g., "SUM_MOD_256", "XOR", "NEG_SUM_MOD_256", "CRC16_CCITT"
    offset: int
    expected_value: int
    actual_value: int
    is_match: bool


@dataclass
class SingleCaptureRGBAnalysis:
    """Analysis of a single RGB capture session."""
    filename: str
    description: str
    reports_count: int
    device_info: HIDInterfaceInfo
    unique_report_ids: List[int]
    unique_lengths: List[int]
    report_types: List[str]
    common_prefix_hex: str
    opcodes: List[str]
    is_aa27_protocol: bool
    potential_terminator: Optional[ReportSummary] = None
    checksum_candidates: List[ChecksumCandidate] = field(default_factory=list)
    reports: List[ReportSummary] = field(default_factory=list)


@dataclass
class ByteDiff:
    """Detailed diff for a single modified byte."""
    report_index: int
    byte_offset: int
    old_value: int
    new_value: int
    delta: int
    old_hex: str
    new_hex: str
    annotation: str = ""


@dataclass
class LEDCorrelationResult:
    """Evaluation of whether a changed LED offset correlates with layout or matrix."""
    modified_byte_offset: int
    probable_led_index: Optional[int] = None
    physical_key_candidate: Optional[str] = None
    matrix_candidate: Optional[Tuple[int, int]] = None
    correlation_type: str = "UNKNOWN"  # "PHYSICAL_LINEAR", "MATRIX_BANK_COL", "UNKNOWN"


@dataclass
class RGBCaptureDiffResult:
    """Comparison of two RGB captures (e.g. baseline vs modified)."""
    base_file: str
    exp_file: str
    action_type: str  # "color", "brightness", "effect", "speed", "per_key"
    byte_diffs: List[ByteDiff] = field(default_factory=list)
    changed_offsets: List[int] = field(default_factory=list)
    invariant_prefix_hex: str = ""
    checksum_found: bool = False
    detected_checksums: List[ChecksumCandidate] = field(default_factory=list)
    is_separate_from_aa27: bool = True
    led_correlations: List[LEDCorrelationResult] = field(default_factory=list)
    summary_text: str = ""


def compute_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    """Compute 16-bit CRC with specified polynomial."""
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def detect_checksums_in_report(data: bytes) -> List[ChecksumCandidate]:
    """
    Test standard checksum algorithms against the last 1 or 2 bytes of a report.
    """
    candidates: List[ChecksumCandidate] = []
    if len(data) < 4:
        return candidates

    # Test 1-byte checksums on the final byte
    last_byte = data[-1]
    payload = data[:-1]

    sum_mod_256 = sum(payload) & 0xFF
    candidates.append(ChecksumCandidate(
        algorithm="SUM_MOD_256",
        offset=len(data) - 1,
        expected_value=sum_mod_256,
        actual_value=last_byte,
        is_match=(sum_mod_256 == last_byte)
    ))

    neg_sum_mod_256 = (-sum(payload)) & 0xFF
    candidates.append(ChecksumCandidate(
        algorithm="NEG_SUM_MOD_256",
        offset=len(data) - 1,
        expected_value=neg_sum_mod_256,
        actual_value=last_byte,
        is_match=(neg_sum_mod_256 == last_byte)
    ))

    xor_sum = 0
    for b in payload:
        xor_sum ^= b
    candidates.append(ChecksumCandidate(
        algorithm="XOR_SUM",
        offset=len(data) - 1,
        expected_value=xor_sum,
        actual_value=last_byte,
        is_match=(xor_sum == last_byte)
    ))

    # Test 2-byte checksums on the last 2 bytes
    if len(data) >= 5:
        last_two_le = data[-2] | (data[-1] << 8)
        last_two_be = (data[-2] << 8) | data[-1]
        payload_16 = data[:-2]

        sum_16 = sum(payload_16) & 0xFFFF
        candidates.append(ChecksumCandidate(
            algorithm="SUM_16_LE",
            offset=len(data) - 2,
            expected_value=sum_16,
            actual_value=last_two_le,
            is_match=(sum_16 == last_two_le)
        ))

        crc_ccitt = compute_crc16(payload_16)
        candidates.append(ChecksumCandidate(
            algorithm="CRC16_CCITT",
            offset=len(data) - 2,
            expected_value=crc_ccitt,
            actual_value=last_two_be,
            is_match=(crc_ccitt == last_two_be)
        ))

    return candidates


def find_longest_common_prefix(byte_sequences: Sequence[bytes]) -> bytes:
    """Find longest common byte prefix across multiple byte strings."""
    if not byte_sequences:
        return b""
    min_len = min(len(s) for s in byte_sequences)
    prefix = bytearray()
    for i in range(min_len):
        b = byte_sequences[0][i]
        if all(s[i] == b for s in byte_sequences[1:]):
            prefix.append(b)
        else:
            break
    return bytes(prefix)


def analyze_single_capture(capture: RawCapture, filename: str = "") -> SingleCaptureRGBAnalysis:
    """
    Analyze reports in a single capture file to extract metadata, opcodes, and formats.
    """
    reports: List[ReportSummary] = []
    dev_meta = capture.device or {}

    collections = dev_meta.get("collections", [])
    primary_up = None
    primary_usage = None
    if collections:
        primary_up = collections[0].get("usage_page")
        primary_usage = collections[0].get("usage")

    for r in capture.reports:
        b = bytes.fromhex(r.data_hex)
        opcode = b[:2].hex().upper() if len(b) >= 2 else ""
        is_aa27 = b.startswith(b"\xAA\x27")

        up = getattr(r, "usage_page", None) or primary_up
        usage = getattr(r, "usage", None) or primary_usage

        reports.append(ReportSummary(
            index=r.index,
            report_type=r.report_type,
            report_id=r.report_id,
            length=r.length,
            data_hex=r.data_hex,
            usage_page=up,
            usage=usage,
            opcode_hex=opcode,
            is_aa27=is_aa27
        ))

    byte_seqs = [r.data_bytes for r in reports]
    common_prefix = find_longest_common_prefix(byte_seqs) if byte_seqs else b""

    unique_ids = sorted(list(set(r.report_id for r in reports)))
    unique_lens = sorted(list(set(r.length for r in reports)))
    report_types = sorted(list(set(r.report_type for r in reports)))
    opcodes = sorted(list(set(r.opcode_hex for r in reports if r.opcode_hex)))

    is_aa27_all = bool(reports and all(r.is_aa27 for r in reports))

    # Detect terminator
    pot_term = None
    for r in reports:
        # Check for known AA2710 or shorter packets
        if r.data_hex.upper().startswith("AA2710") or (r.length < 64 and r.index == len(reports) - 1):
            pot_term = r
            break

    # Checksum candidate search on the last report
    checksum_cands = []
    if reports:
        checksum_cands = [c for c in detect_checksums_in_report(reports[-1].data_bytes) if c.is_match]

    dev_info = HIDInterfaceInfo(
        vendor_id=dev_meta.get("vendor_id", "0x0C45"),
        product_id=dev_meta.get("product_id", "0x80D6"),
        product_name=dev_meta.get("product_name", "IO by Red Square Type 84 Magnetic Black"),
        usage_page=primary_up,
        usage=primary_usage,
        collections=collections
    )

    return SingleCaptureRGBAnalysis(
        filename=filename,
        description=capture.description,
        reports_count=len(reports),
        device_info=dev_info,
        unique_report_ids=unique_ids,
        unique_lengths=unique_lens,
        report_types=report_types,
        common_prefix_hex=common_prefix.hex().upper(),
        opcodes=opcodes,
        is_aa27_protocol=is_aa27_all,
        potential_terminator=pot_term,
        checksum_candidates=checksum_cands,
        reports=reports
    )


def diff_rgb_captures(
    base_cap: RawCapture,
    exp_cap: RawCapture,
    action_type: str = "unknown",
    base_name: str = "base",
    exp_name: str = "exp"
) -> RGBCaptureDiffResult:
    """
    Compare two RGB captures report-by-report and byte-by-byte.
    """
    diffs: List[ByteDiff] = []
    changed_offsets: Set[int] = set()

    min_reports = min(len(base_cap.reports), len(exp_cap.reports))

    for r_idx in range(min_reports):
        base_b = bytes.fromhex(base_cap.reports[r_idx].data_hex)
        exp_b = bytes.fromhex(exp_cap.reports[r_idx].data_hex)

        min_len = min(len(base_b), len(exp_b))
        for b_idx in range(min_len):
            if base_b[b_idx] != exp_b[b_idx]:
                changed_offsets.add(b_idx)
                delta = exp_b[b_idx] - base_b[b_idx]
                diffs.append(ByteDiff(
                    report_index=r_idx,
                    byte_offset=b_idx,
                    old_value=base_b[b_idx],
                    new_value=exp_b[b_idx],
                    delta=delta,
                    old_hex=f"0x{base_b[b_idx]:02X}",
                    new_hex=f"0x{exp_b[b_idx]:02X}"
                ))

    # Test checksum on experimental reports
    detected_ck = []
    if exp_cap.reports:
        for r in exp_cap.reports:
            for ck in detect_checksums_in_report(bytes.fromhex(r.data_hex)):
                if ck.is_match:
                    detected_ck.append(ck)

    # Invariant prefix across both base and exp captures
    all_bytes = [bytes.fromhex(r.data_hex) for r in (base_cap.reports + exp_cap.reports)]
    prefix = find_longest_common_prefix(all_bytes) if all_bytes else b""

    # Separate from AA27 check
    is_separate = True
    if exp_cap.reports:
        first_hex = exp_cap.reports[0].data_hex.upper()
        if first_hex.startswith("AA27"):
            is_separate = False

    # LED correlation evaluation
    led_correlations = []
    if action_type == "per_key" and changed_offsets:
        for offset in sorted(changed_offsets):
            led_correlations.append(evaluate_led_offset_correlation(offset))

    # Summary text
    lines = [
        f"RGB Diff Analysis: {base_name} vs {exp_name} (Action: {action_type})",
        f"- Reports count: base={len(base_cap.reports)}, exp={len(exp_cap.reports)}",
        f"- Total byte differences: {len(diffs)}",
        f"- Unique modified byte offsets: {sorted(list(changed_offsets))}",
        f"- Command prefix: {prefix.hex().upper() if prefix else 'None'}",
        f"- Separate from AA 27 key config: {is_separate}",
    ]
    if detected_ck:
        lines.append(f"- Matched checksum algorithms: {[c.algorithm for c in detected_ck]}")
    else:
        lines.append("- Matched checksum algorithms: None (no standard checksum detected)")

    return RGBCaptureDiffResult(
        base_file=base_name,
        exp_file=exp_name,
        action_type=action_type,
        byte_diffs=diffs,
        changed_offsets=sorted(list(changed_offsets)),
        invariant_prefix_hex=prefix.hex().upper(),
        checksum_found=bool(detected_ck),
        detected_checksums=detected_ck,
        is_separate_from_aa27=is_separate,
        led_correlations=led_correlations,
        summary_text="\n".join(lines)
    )


def evaluate_led_offset_correlation(byte_offset: int) -> LEDCorrelationResult:
    """
    Check if a changed byte offset correlates with LED indices (linear 0..83 or matrix bank/col).
    """
    # Assuming 3 bytes per LED (RGB) or 1 byte index
    # Option A: RGB triplet index = (byte_offset - header_len) // 3
    # Option B: Direct LED index = byte_offset - header_len
    # We test with common header sizes 4, 5, 8
    for header in (4, 5, 8):
        if byte_offset >= header:
            direct_idx = byte_offset - header
            triplet_idx = (byte_offset - header) // 3
            if 0 <= triplet_idx < 84:
                return LEDCorrelationResult(
                    modified_byte_offset=byte_offset,
                    probable_led_index=triplet_idx,
                    correlation_type="PHYSICAL_LINEAR_RGB_TRIPLET"
                )
            if 0 <= direct_idx < 84:
                return LEDCorrelationResult(
                    modified_byte_offset=byte_offset,
                    probable_led_index=direct_idx,
                    correlation_type="PHYSICAL_LINEAR_BYTE"
                )

    return LEDCorrelationResult(
        modified_byte_offset=byte_offset,
        correlation_type="UNKNOWN"
    )


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Type 84 RGB Protocol Analyzer")
    parser.add_argument("files", nargs="+", help="One capture file (for single analysis) or two (for diff)")
    parser.add_argument("--action", default="unknown", choices=["color", "brightness", "effect", "speed", "per_key", "unknown"])
    args = parser.parse_args()

    if len(args.files) == 1:
        path = Path(args.files[0])
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            return 1
        cap = RawCapture.load_json(path)
        res = analyze_single_capture(cap, filename=path.name)
        print("=" * 60)
        print(f"RGB Capture: {res.filename}")
        print(f"Description: {res.description}")
        print(f"Reports Count: {res.reports_count}")
        print(f"Device: VID={res.device_info.vendor_id}, PID={res.device_info.product_id}")
        if res.device_info.collections:
            for i, c in enumerate(res.device_info.collections):
                print(f"  Collection #{i}: UsagePage={c.get('usage_page')}, Usage={c.get('usage')}")
        print(f"Report IDs: {res.unique_report_ids}")
        print(f"Lengths: {res.unique_lengths}")
        print(f"Types: {res.report_types}")
        print(f"Opcodes: {res.opcodes}")
        print(f"Common Prefix: {res.common_prefix_hex}")
        print(f"Is AA 27 Protocol: {res.is_aa27_protocol}")
        if res.potential_terminator:
            print(f"Terminator Detected: #{res.potential_terminator.index} hex={res.potential_terminator.data_hex[:24]}...")
        if res.checksum_candidates:
            for ck in res.checksum_candidates:
                print(f"Checksum Match: {ck.algorithm} @offset={ck.offset} val=0x{ck.actual_value:02X}")
        print("=" * 60)
        return 0

    elif len(args.files) >= 2:
        base_path = Path(args.files[0])
        exp_path = Path(args.files[1])
        base_cap = RawCapture.load_json(base_path)
        exp_cap = RawCapture.load_json(exp_path)
        diff_res = diff_rgb_captures(base_cap, exp_cap, action_type=args.action, base_name=base_path.name, exp_name=exp_path.name)
        print("=" * 60)
        print(diff_res.summary_text)
        print("-" * 60)
        print(f"Detailed Byte Differences ({len(diff_res.byte_diffs)}):")
        for d in diff_res.byte_diffs[:50]:
            print(f"  [Report #{d.report_index} Offset +{d.byte_offset:02d}] {d.old_hex} ({d.old_value:3d}) -> {d.new_hex} ({d.new_value:3d})  delta={d.delta:+d}")
        if len(diff_res.byte_diffs) > 50:
            print(f"  ... and {len(diff_res.byte_diffs) - 50} more bytes")
        if diff_res.led_correlations:
            print("-" * 60)
            print("LED Correlation Candidates:")
            for lc in diff_res.led_correlations:
                print(f"  Offset +{lc.modified_byte_offset:02d} -> Probable LED Index #{lc.probable_led_index} ({lc.correlation_type})")
        print("=" * 60)
        return 0

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

