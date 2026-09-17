"""
Passive dump importers to convert external captures (Wireshark, hex text)
into the unified RawCapture JSON format.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from keyboard_re.models import (
    EXPECTED_PID,
    EXPECTED_VID,
    REPORT_SIZE,
    RawCapture,
    RawReport,
)


def import_hex_lines(
    lines: List[str],
    description: str = "Imported from hex dump",
    source_filename: Optional[str] = None
) -> RawCapture:
    """
    Import raw hex strings (one per line, 64 or 65 bytes in hex).
    """
    reports: List[RawReport] = []
    idx = 0
    for line in lines:
        cleaned = line.strip().replace(" ", "").replace(":", "").replace("-", "")
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("//"):
            continue

        raw_bytes = bytes.fromhex(cleaned)
        if len(raw_bytes) == REPORT_SIZE + 1 and raw_bytes[0] == 0x00:
            raw_bytes = raw_bytes[1:]

        if len(raw_bytes) != REPORT_SIZE:
            raise ValueError(
                f"Line {idx+1}: report length is {len(raw_bytes)} bytes, expected {REPORT_SIZE}"
            )

        reports.append(
            RawReport(
                index=idx,
                timestamp_ms=float(idx * 10),
                report_type="output",
                report_id=0,
                length=REPORT_SIZE,
                data_hex=raw_bytes.hex(),
            )
        )
        idx += 1

    return RawCapture(
        version="1.0",
        device={
            "vendor_id": f"0x{EXPECTED_VID:04X}",
            "product_id": f"0x{EXPECTED_PID:04X}",
            "product_name": "IO by Red Square Type 84 Magnetic Black",
            "imported_from": source_filename or "hex_dump"
        },
        captured_at=datetime.now(timezone.utc).isoformat(),
        description=description,
        reports_count=len(reports),
        reports=reports,
    )


def import_wireshark_json(
    json_path: Path | str,
    description: str = "Imported from Wireshark JSON"
) -> RawCapture:
    """
    Import packet dissections exported from Wireshark as JSON.
    Looks for usbhid.data or usb.capdata in packets.
    """
    path = Path(json_path)
    with open(path, "r", encoding="utf-8") as f:
        packets = json.load(f)

    reports: List[RawReport] = []
    idx = 0

    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        hex_data = None
        for key in ("usbhid.data", "usb.capdata", "data.data"):
            if key in layers:
                val = layers[key]
                if isinstance(val, str):
                    hex_data = val.replace(":", "")
                elif isinstance(val, list) and val:
                    hex_data = str(val[0]).replace(":", "")
                break

        if hex_data:
            raw_bytes = bytes.fromhex(hex_data)
            if len(raw_bytes) == REPORT_SIZE + 1 and raw_bytes[0] == 0x00:
                raw_bytes = raw_bytes[1:]

            if len(raw_bytes) == REPORT_SIZE:
                reports.append(
                    RawReport(
                        index=idx,
                        timestamp_ms=float(idx * 10),
                        report_type="output",
                        report_id=0,
                        length=REPORT_SIZE,
                        data_hex=raw_bytes.hex(),
                    )
                )
                idx += 1

    return RawCapture(
        version="1.0",
        device={
            "vendor_id": f"0x{EXPECTED_VID:04X}",
            "product_id": f"0x{EXPECTED_PID:04X}",
            "product_name": "IO by Red Square Type 84 Magnetic Black",
            "imported_from": str(path.name)
        },
        captured_at=datetime.now(timezone.utc).isoformat(),
        description=description,
        reports_count=len(reports),
        reports=reports,
    )
