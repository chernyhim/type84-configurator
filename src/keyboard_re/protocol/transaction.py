"""
Write Transaction and READ-BACK Verification Engine for Type 84.

Implements safe, synchronized write transactions:
send packet -> wait ACK -> validate ACK -> next packet

And closed-loop read-back verification:
WRITE -> ACK -> READ -> PARSE -> COMPARE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import struct
from typing import TYPE_CHECKING, Any, List, Optional, Sequence

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.transport import HidTransport

if TYPE_CHECKING:
    from keyboard_re.models.state import KeyboardSnapshot
    from keyboard_re.protocol.diff import StateDiff


class TransactionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class VerificationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class TransactionError(Exception):
    """Raised when a write transaction fails or encounters an invalid ACK."""
    pass


@dataclass
class TransactionResult:
    status: TransactionStatus
    packets_sent: int
    acks_received: int
    error: Optional[str] = None


@dataclass
class VerificationResult:
    status: VerificationStatus
    applied_diffs: List[StateDiff] = field(default_factory=list)
    missing_diffs: List[StateDiff] = field(default_factory=list)
    unexpected_diffs: List[StateDiff] = field(default_factory=list)
    summary: str = ""


def write_transaction(
    transport: HidTransport,
    packets: Sequence[bytes],
    expected_ack_opcode: int = 0x27,
    timeout: float = 1.0,
    verbose: bool = False,
) -> TransactionResult:
    """
    Execute a synchronized write transaction over an abstract HID transport.
    
    Each packet is sent via send_report, and its echo ACK is validated:
    - Prefix: 0x55
    - Opcode: expected_ack_opcode (e.g. 0x27 for Hall, 0x23 for Global RGB, 0x24 for Per-Key RGB)
    - Address matching packet address
    """
    packets_sent = 0
    acks_received = 0

    for idx, packet in enumerate(packets):
        validate_report_size(packet, REPORT_SIZE)
        req = bytes(packet)

        if verbose:
            print(f"TX #{idx+1}:")
            print(f"  {req[:16].hex(' ').upper()} ...")

        # 1. Send report
        try:
            transport.send_report(0, req)
            packets_sent += 1
        except Exception as e:
            return TransactionResult(
                status=TransactionStatus.FAILED if packets_sent == 1 else TransactionStatus.PARTIAL,
                packets_sent=packets_sent,
                acks_received=acks_received,
                error=f"Send error on packet #{idx}: {e}",
            )

        # 2. Wait for ACK
        try:
            ack = transport.receive_report(timeout=timeout)
        except Exception as e:
            return TransactionResult(
                status=TransactionStatus.PARTIAL if acks_received > 0 else TransactionStatus.FAILED,
                packets_sent=packets_sent,
                acks_received=acks_received,
                error=f"Timeout/Error waiting for ACK on packet #{idx}: {e}",
            )

        validate_report_size(ack, REPORT_SIZE)

        if verbose:
            print(f"RX #{idx+1}:")
            print(f"  {ack[:16].hex(' ').upper()} ...")

        # 3. Validate ACK
        if ack[0] != 0x55:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                packets_sent=packets_sent,
                acks_received=acks_received,
                error=f"Invalid ACK prefix 0x{ack[0]:02X}, expected 0x55 on packet #{idx}",
            )

        if ack[1] != expected_ack_opcode:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                packets_sent=packets_sent,
                acks_received=acks_received,
                error=f"Invalid ACK opcode 0x{ack[1]:02X}, expected 0x{expected_ack_opcode:02X} on packet #{idx}",
            )

        req_addr = struct.unpack_from("<H", req, 3)[0]
        ack_addr = struct.unpack_from("<H", ack, 3)[0]
        if req_addr != ack_addr:
            return TransactionResult(
                status=TransactionStatus.FAILED,
                packets_sent=packets_sent,
                acks_received=acks_received,
                error=f"Address mismatch in ACK: expected 0x{req_addr:04X}, got 0x{ack_addr:04X} on packet #{idx}",
            )

        acks_received += 1

    return TransactionResult(
        status=TransactionStatus.SUCCESS,
        packets_sent=packets_sent,
        acks_received=acks_received,
        error=None,
    )


def verify_write(
    before: KeyboardSnapshot,
    intended: KeyboardSnapshot,
    actual_after: KeyboardSnapshot,
) -> VerificationResult:
    """
    Verify that write modifications were successfully applied by comparing:
    - Intended diff: before -> intended
    - Actual diff: before -> actual_after
    
    Categorizes results into applied, missing, and unexpected diffs.
    """
    from keyboard_re.protocol.diff import compare_snapshots

    intended_diffs = compare_snapshots(before, intended)
    actual_diffs = compare_snapshots(before, actual_after)

    # Key identification helper
    def diff_key(d: StateDiff) -> Tuple[Any, ...]:
        return (d.subsystem, d.target, d.parameter, d.address, d.profile_id)

    actual_map = {diff_key(d): d for d in actual_diffs}
    intended_map = {diff_key(d): d for d in intended_diffs}

    applied: List[StateDiff] = []
    missing: List[StateDiff] = []
    unexpected: List[StateDiff] = []

    for k, d_int in intended_map.items():
        if k in actual_map:
            d_act = actual_map[k]
            if d_act.new_value == d_int.new_value:
                applied.append(d_act)
            else:
                missing.append(d_int)
        else:
            missing.append(d_int)

    for k, d_act in actual_map.items():
        if k not in intended_map:
            unexpected.append(d_act)

    if not intended_diffs:
        status = VerificationStatus.SUCCESS
        summary = "No modifications intended. State verified identical."
    elif len(applied) == len(intended_diffs) and not unexpected:
        status = VerificationStatus.SUCCESS
        summary = f"All {len(applied)} modifications successfully verified."
    elif len(applied) > 0:
        status = VerificationStatus.PARTIAL
        summary = f"Partial match: {len(applied)} applied, {len(missing)} missing, {len(unexpected)} unexpected."
    else:
        status = VerificationStatus.FAILED
        summary = f"Write verification failed: 0/{len(intended_diffs)} modifications applied."

    return VerificationResult(
        status=status,
        applied_diffs=applied,
        missing_diffs=missing,
        unexpected_diffs=unexpected,
        summary=summary,
    )
