"""
Read / State-Sync Protocol Analyzer for IO by Red Square Type 84 Magnetic Black.

Analyzes bidirectional passive WebHID traffic from https://web.io.vision/ to determine:
1. Which HID interfaces participate in communication.
2. Directions of traffic (HOST -> DEVICE vs DEVICE -> HOST).
3. Presence of inputreport, receiveFeatureReport, sendReport, sendFeatureReport.
4. Request-response pairs and handshake sequences.
5. Content of device responses (Hall config, RGB config, Profile sync).
6. Constant headers, report IDs, lengths, and checksums.

NO active transmission or device writing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SyncEvent:
    index: int
    timestamp_ms: float
    direction: str  # "HOST -> DEVICE", "DEVICE -> HOST", "LIFECYCLE"
    event_type: str  # "sendReport", "sendFeatureReport", "receiveFeatureReport", "inputreport", "deviceOpen"
    report_type: str  # "output", "feature", "input", "none"
    device_id: str
    usage_page: Optional[str] = None
    usage: Optional[str] = None
    report_id: Optional[int] = None
    length: int = 0
    data_hex: str = ""

    @property
    def opcode(self) -> str:
        if self.data_hex and len(self.data_hex) >= 4:
            return self.data_hex[:4].upper()
        return ""

    @property
    def prefix_6(self) -> str:
        if self.data_hex and len(self.data_hex) >= 6:
            return self.data_hex[:6].upper()
        return ""


@dataclass
class RequestResponsePair:
    request: SyncEvent
    response: SyncEvent
    delta_ms: float
    is_same_interface: bool


@dataclass
class StateSyncAnalysis:
    filename: str
    target: str
    description: str
    total_events: int
    devices: List[Dict[str, Any]]
    events_by_direction: Dict[str, int]
    events_by_type: Dict[str, int]
    active_interfaces: List[str]
    timeline: List[str]
    pairs: List[RequestResponsePair]
    observed_opcodes_host: List[str]
    observed_opcodes_device: List[str]
    has_inputreport: bool
    has_receive_feature: bool
    has_hall_sync: bool
    has_rgb_sync: bool
    has_handshake: bool
    summary: str


def load_sync_events(path: Path | str) -> Tuple[List[SyncEvent], List[Dict[str, Any]], Dict[str, Any]]:
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    devices = data.get("devices", [])
    raw_events = data.get("events", data.get("reports", []))

    events: List[SyncEvent] = []
    for i, e in enumerate(raw_events):
        direction = e.get("direction")
        evt_type = e.get("event_type", e.get("report_type", "output"))

        # Invert/infer direction if not explicitly given in v1 captures
        if not direction:
            if evt_type in ("output", "sendReport", "sendFeatureReport"):
                direction = "HOST -> DEVICE"
            elif evt_type in ("input", "inputreport", "receiveFeatureReport"):
                direction = "DEVICE -> HOST"
            else:
                direction = "HOST -> DEVICE"

        events.append(SyncEvent(
            index=e.get("index", i),
            timestamp_ms=float(e.get("timestamp_ms", 0.0)),
            direction=direction,
            event_type=evt_type,
            report_type=e.get("report_type", "output"),
            device_id=e.get("device_id", "dev_0"),
            usage_page=e.get("usage_page"),
            usage=e.get("usage"),
            report_id=e.get("report_id"),
            length=int(e.get("length", len(e.get("data_hex", "")) // 2)),
            data_hex=e.get("data_hex", "").strip()
        ))

    return events, devices, data


def analyze_sync_capture(path: Path | str) -> StateSyncAnalysis:
    events, devices, meta = load_sync_events(path)
    p = Path(path)

    by_dir: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    interfaces_set = set()
    opcodes_host = set()
    opcodes_dev = set()

    for e in events:
        by_dir[e.direction] = by_dir.get(e.direction, 0) + 1
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        if e.usage_page and e.usage:
            interfaces_set.add(f"{e.usage_page}:{e.usage}")
        if e.direction == "HOST -> DEVICE" and e.opcode:
            opcodes_host.add(e.opcode)
        elif e.direction == "DEVICE -> HOST" and e.opcode:
            opcodes_dev.add(e.opcode)

    # Timeline reconstruction
    timeline: List[str] = []
    for e in events:
        up = f"[{e.usage_page}:{e.usage}]" if e.usage_page else ""
        op = f"[{e.prefix_6}]" if e.prefix_6 else ""
        hex_preview = e.data_hex[:32] + "..." if len(e.data_hex) > 32 else e.data_hex
        timeline.append(
            f"t={e.timestamp_ms:8.2f}ms | {e.direction:<14} | {e.device_id:<5} {up:<15} | {e.event_type:<20} ID={str(e.report_id):<2} len={e.length:<3} | {op:<8} {hex_preview}"
        )

    # Request -> Response matching
    pairs: List[RequestResponsePair] = []
    for i, req in enumerate(events):
        if req.direction == "HOST -> DEVICE":
            # Find nearest subsequent DEVICE -> HOST event
            for j in range(i + 1, len(events)):
                resp = events[j]
                if resp.direction == "DEVICE -> HOST":
                    delta = resp.timestamp_ms - req.timestamp_ms
                    is_same = (req.device_id == resp.device_id)
                    pairs.append(RequestResponsePair(
                        request=req,
                        response=resp,
                        delta_ms=delta,
                        is_same_interface=is_same
                    ))
                    break

    has_input = any(e.event_type == "inputreport" for e in events)
    has_rec_feat = any(e.event_type == "receiveFeatureReport" for e in events)

    # Check for Hall config sync (1008 bytes or AA 27 patterns in responses)
    has_hall = any(
        e.direction == "DEVICE -> HOST" and ("AA27" in e.prefix_6 or e.length >= 56)
        for e in events
    )
    # Check for RGB config sync (AA 23 or AA 24 patterns in responses)
    has_rgb = any(
        e.direction == "DEVICE -> HOST" and ("AA23" in e.prefix_6 or "AA24" in e.prefix_6)
        for e in events
    )
    # Check for handshake (short query/reply at start)
    has_hs = bool(pairs and pairs[0].delta_ms < 500)

    summary_lines = [
        f"State-Sync Analysis for: {p.name}",
        f"- Target: {meta.get('target', 'https://web.io.vision/')}",
        f"- Description: {meta.get('description', '')}",
        f"- Total Events: {len(events)}",
        f"- Events by Direction: {by_dir}",
        f"- Events by Type: {by_type}",
        f"- Active Interfaces: {list(interfaces_set)}",
        f"- Host Opcodes: {sorted(list(opcodes_host))}",
        f"- Device Opcodes: {sorted(list(opcodes_dev))}",
        f"- Request-Response Pairs Found: {len(pairs)}",
        f"- InputReport Observed: {has_input}",
        f"- ReceiveFeatureReport Observed: {has_rec_feat}",
        f"- Hall State Returned: {has_hall}",
        f"- RGB State Returned: {has_rgb}",
        f"- Handshake Detected: {has_hs}",
    ]

    return StateSyncAnalysis(
        filename=p.name,
        target=meta.get("target", "https://web.io.vision/"),
        description=meta.get("description", ""),
        total_events=len(events),
        devices=devices,
        events_by_direction=by_dir,
        events_by_type=by_type,
        active_interfaces=sorted(list(interfaces_set)),
        timeline=timeline,
        pairs=pairs,
        observed_opcodes_host=sorted(list(opcodes_host)),
        observed_opcodes_device=sorted(list(opcodes_dev)),
        has_inputreport=has_input,
        has_receive_feature=has_rec_feat,
        has_hall_sync=has_hall,
        has_rgb_sync=has_rgb,
        has_handshake=has_hs,
        summary="\n".join(summary_lines)
    )


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Type 84 Read / State-Sync Protocol Analyzer")
    parser.add_argument("capture_file", help="Path to WebHID sync capture JSON")
    parser.add_argument("--timeline", action="store_true", help="Print full event timeline")
    parser.add_argument("--pairs", action="store_true", help="Print request-response pairs")
    args = parser.parse_args()

    p = Path(args.capture_file)
    if not p.exists():
        print(f"Error: file not found: {p}", file=sys.stderr)
        return 1

    res = analyze_sync_capture(p)
    print("=" * 70)
    print(res.summary)
    print("=" * 70)

    if args.pairs and res.pairs:
        print("\nRequest -> Response Pairs:")
        for idx, pair in enumerate(res.pairs):
            req = pair.request
            resp = pair.response
            print(f"Pair #{idx+1} (dt={pair.delta_ms:.2f}ms):")
            print(f"  REQ: [{req.event_type}] [ID={req.report_id}] {req.data_hex[:32]}...")
            print(f"  RSP: [{resp.event_type}] [ID={resp.report_id}] {resp.data_hex[:32]}...")

    if args.timeline:
        print("\nFull Event Timeline:")
        for line in res.timeline:
            print(line)

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
