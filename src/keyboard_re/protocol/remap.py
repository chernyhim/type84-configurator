"""
Key Remapping (AA 22 / AA 12) Production Write Pipeline for Type 84.

Features:
- Fixed 512-byte Remap L1 image (128 slots x 4 bytes).
- 10-chunk sequential write (9 x 56B + 1 x 8B = 512B) with opcode AA 22.
- Synchronous ACK validation: opcode 55 22, size, address, and payload echo.
- Closed-loop safety: read-before-write baseline validation, empty diff early-out,
  and mandatory byte-for-byte read-back verification.
- Explicit RemapWritePolicy safety gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.read import read_keymap_table
from keyboard_re.protocol.transport import HidTransport

# Protocol constants
REMAP_IMAGE_SIZE: int = 512
REMAP_SLOT_SIZE: int = 4
REMAP_SLOT_COUNT: int = 128
REMAP_WRITE_OPCODE: int = 0x22
REMAP_READ_OPCODE: int = 0x12
REMAP_CHUNK_SIZE: int = 56
REMAP_TAIL_SIZE: int = 8
REMAP_CHUNK_COUNT: int = 10


@dataclass(frozen=True)
class RemapRecord:
    """
    Typed 4-byte key remap record from the 512-byte matrix table.

    Structure:
    - prefix: Byte 0 (modifier / extended prefix, default 0x00)
    - scancode: Byte 1 (standard USB HID Usage ID from Page 0x07)
    - special: Byte 2 (special function / consumer / hardware control code)
    - type: Byte 3 (function type: 0x02=Standard HID, 0x03=Extended, 0x0D=Hardware/RGB, 0x00=Unbound)
    """
    prefix: int = 0
    scancode: int = 0
    special: int = 0
    type: int = 0x02

    @property
    def function_type(self) -> int:
        """Alias for type (matching KeyRemapRecord)."""
        return self.type

    @property
    def is_empty(self) -> bool:
        """Returns True if the slot is unbound / all zeros."""
        return self.prefix == 0 and self.scancode == 0 and self.special == 0 and self.type == 0

    def to_bytes(self) -> bytes:
        """Encode to 4-byte wire format."""
        return encode_record(self)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> RemapRecord:
        """Decode from 4-byte wire format."""
        return decode_record(data)


class RemapByteDiff(NamedTuple):
    """Represents a single byte difference between two 512-byte remap images."""
    offset: int
    before: int
    after: int
    slot: int = 0
    slot_byte: int = 0


class RemapAckError(ValueError):
    """Raised when an incoming ACK packet fails protocol validation."""
    pass


@dataclass(frozen=True)
class RemapAck:
    """Parsed device ACK report (55 22 <sz> <addr_lo> <addr_hi> ...)."""
    opcode: int
    size: int
    address: int
    payload_echo: bytes
    raw: bytes


@dataclass
class RemapWritePolicy:
    """
    Explicit safety gate for Remap L1 write transactions.
    
    Prevents unverified or accidental physical writes.
    """
    require_confirmation: bool = True
    require_baseline: bool = True
    require_readback: bool = True
    confirmed: bool = False
    timeout_ms: int = 1000


@dataclass
class RemapWriteResult:
    """Structured result of a Remap L1 write transaction."""
    success: bool
    chunks_sent: int
    acks_received: int
    changed_bytes: List[RemapByteDiff]
    changed_slots: List[int]
    readback_verified: bool
    baseline_image: bytes
    written_image: bytes
    readback_image: bytes
    summary: str = ""
    error_message: Optional[str] = None


def slot_offset(slot: int) -> int:
    """
    Calculate byte offset for a given logical slot (0..127).
    """
    if not (0 <= slot < REMAP_SLOT_COUNT):
        raise ValueError(
            f"Invalid remap slot index {slot}: must be 0..{REMAP_SLOT_COUNT - 1}"
        )
    return slot * REMAP_SLOT_SIZE


def encode_record(record: RemapRecord) -> bytes:
    """
    Encode a RemapRecord into 4 bytes.
    """
    return bytes([
        record.prefix & 0xFF,
        record.scancode & 0xFF,
        record.special & 0xFF,
        record.type & 0xFF,
    ])


def decode_record(data: bytes | bytearray) -> RemapRecord:
    """
    Decode 4 bytes into a RemapRecord.
    """
    if len(data) < REMAP_SLOT_SIZE:
        raise ValueError(
            f"Expected at least {REMAP_SLOT_SIZE} bytes for RemapRecord, got {len(data)}"
        )
    return RemapRecord(
        prefix=int(data[0]),
        scancode=int(data[1]),
        special=int(data[2]),
        type=int(data[3]),
    )


def parse_image(data: bytes | bytearray) -> List[RemapRecord]:
    """
    Parse a 512-byte table image into a list of 128 RemapRecord objects.
    """
    if len(data) != REMAP_IMAGE_SIZE:
        raise ValueError(
            f"Expected exactly {REMAP_IMAGE_SIZE} bytes for remap image, got {len(data)}"
        )
    records: List[RemapRecord] = []
    for slot in range(REMAP_SLOT_COUNT):
        offset = slot * REMAP_SLOT_SIZE
        records.append(decode_record(data[offset : offset + REMAP_SLOT_SIZE]))
    return records


def build_image(records: Sequence[RemapRecord]) -> bytes:
    """
    Construct a 512-byte table image from 128 RemapRecord objects.
    """
    if len(records) != REMAP_SLOT_COUNT:
        raise ValueError(
            f"Expected exactly {REMAP_SLOT_COUNT} records, got {len(records)}"
        )
    buf = bytearray()
    for rec in records:
        buf.extend(encode_record(rec))
    return bytes(buf)


def diff_images(
    before: bytes | bytearray,
    after: bytes | bytearray,
) -> List[RemapByteDiff]:
    """
    Compute exact byte-level differences between two 512-byte remap images.
    """
    if len(before) != REMAP_IMAGE_SIZE:
        raise ValueError(f"Before image must be {REMAP_IMAGE_SIZE} bytes, got {len(before)}")
    if len(after) != REMAP_IMAGE_SIZE:
        raise ValueError(f"After image must be {REMAP_IMAGE_SIZE} bytes, got {len(after)}")

    diffs: List[RemapByteDiff] = []
    for i in range(REMAP_IMAGE_SIZE):
        b_val = before[i]
        a_val = after[i]
        if b_val != a_val:
            slot = i // REMAP_SLOT_SIZE
            slot_byte = i % REMAP_SLOT_SIZE
            diffs.append(RemapByteDiff(
                offset=i,
                before=b_val,
                after=a_val,
                slot=slot,
                slot_byte=slot_byte,
            ))
    return diffs


def build_remap_write_packet(address: int, payload: bytes | bytearray, is_last: bool = False) -> bytes:
    """
    Build a 64-byte write report for Remap L1 (AA 22 <sz> <addr_lo> <addr_hi> 00 <is_last> 00 <payload...>).
    Uses standard 8-byte HID report header.
    """
    payload_len = len(payload)
    if payload_len == 0 or payload_len > REMAP_CHUNK_SIZE:
        raise ValueError(
            f"Payload length must be between 1 and {REMAP_CHUNK_SIZE}, got {payload_len}"
        )
    if not (0 <= address <= REMAP_IMAGE_SIZE - payload_len):
        raise ValueError(
            f"Invalid address 0x{address:04X} ({address}) for payload length {payload_len}"
        )

    packet = bytearray(REPORT_SIZE)
    packet[0] = 0xAA
    packet[1] = REMAP_WRITE_OPCODE
    packet[2] = payload_len
    struct.pack_into("<H", packet, 3, address)
    packet[5] = 0x00
    packet[6] = 0x01 if is_last else 0x00
    packet[7] = 0x00
    packet[8 : 8 + payload_len] = payload
    return bytes(packet)


def build_remap_write_chunks(image: bytes | bytearray) -> List[Tuple[int, bytes, bytes]]:
    """
    Generate the 10 sequential write chunks (address, payload, 64-byte packet)
    for a 512-byte table image.
    
    9 chunks of 56 bytes (0..503, is_last=False) + 1 tail chunk of 8 bytes (504..511, is_last=True).
    """
    if len(image) != REMAP_IMAGE_SIZE:
        raise ValueError(
            f"Remap image must be exactly {REMAP_IMAGE_SIZE} bytes, got {len(image)}"
        )

    chunks: List[Tuple[int, bytes, bytes]] = []

    # 1. 9 chunks of 56 bytes
    for i in range(9):
        addr = i * REMAP_CHUNK_SIZE
        payload = bytes(image[addr : addr + REMAP_CHUNK_SIZE])
        pkt = build_remap_write_packet(addr, payload, is_last=False)
        chunks.append((addr, payload, pkt))

    # 2. 1 tail chunk of 8 bytes at address 504
    tail_addr = 9 * REMAP_CHUNK_SIZE  # 504
    tail_payload = bytes(image[tail_addr : tail_addr + REMAP_TAIL_SIZE])
    tail_pkt = build_remap_write_packet(tail_addr, tail_payload, is_last=True)
    chunks.append((tail_addr, tail_payload, tail_pkt))

    return chunks


def parse_remap_ack(report: bytes | bytearray) -> RemapAck:
    """
    Parse a 64-byte device ACK report (55 22 <sz> <addr_lo> <addr_hi> 00 <is_last> 00 <payload_echo...>).
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if rep[0] != 0x55:
        raise RemapAckError(f"Invalid ACK prefix 0x{rep[0]:02X}, expected 0x55")
    if rep[1] != REMAP_WRITE_OPCODE:
        raise RemapAckError(
            f"Invalid ACK opcode 0x{rep[1]:02X}, expected 0x{REMAP_WRITE_OPCODE:02X}"
        )

    size = rep[2]
    addr = struct.unpack_from("<H", rep, 3)[0]
    payload_echo = rep[8 : 8 + size]

    return RemapAck(
        opcode=rep[1],
        size=size,
        address=addr,
        payload_echo=payload_echo,
        raw=rep,
    )


def validate_remap_ack(
    expected_address: int,
    expected_payload: bytes | bytearray,
    response: bytes | bytearray,
) -> bool:
    """
    Validate device ACK report against expected address and payload.
    Raises RemapAckError on any mismatch.
    """
    ack = parse_remap_ack(response)
    expected_len = len(expected_payload)

    if ack.size != expected_len:
        raise RemapAckError(
            f"ACK size mismatch at address 0x{expected_address:04X}: "
            f"expected {expected_len}, got {ack.size}"
        )

    if ack.address != expected_address:
        raise RemapAckError(
            f"ACK address mismatch: expected 0x{expected_address:04X} ({expected_address}), "
            f"got 0x{ack.address:04X} ({ack.address})"
        )

    expected_bytes = bytes(expected_payload)
    if ack.payload_echo != expected_bytes:
        raise RemapAckError(
            f"ACK payload echo mismatch at address 0x{expected_address:04X}"
        )

    return True


def patch_remap_image(
    baseline: bytes | bytearray,
    slot: int,
    new_record: RemapRecord,
) -> bytes:
    """
    Create a target image from baseline by modifying strictly one slot.
    
    Preserves all existing factory/phantom slots and verifies that diff
    is strictly restricted to the 4 bytes of the target slot.
    """
    if len(baseline) != REMAP_IMAGE_SIZE:
        raise ValueError(
            f"Baseline must be {REMAP_IMAGE_SIZE} bytes, got {len(baseline)}"
        )
    if not (0 <= slot < REMAP_SLOT_COUNT):
        raise ValueError(
            f"Slot {slot} out of range (0..{REMAP_SLOT_COUNT - 1})"
        )

    target = bytearray(baseline)
    offset = slot_offset(slot)
    target[offset : offset + REMAP_SLOT_SIZE] = encode_record(new_record)

    diffs = diff_images(baseline, target)
    for d in diffs:
        if d.slot != slot or not (offset <= d.offset < offset + REMAP_SLOT_SIZE):
            raise RuntimeError(
                f"Unexpected diff outside target slot {slot}: offset 0x{d.offset:04X}"
            )

    return bytes(target)


def _send(transport: HidTransport, report_id: int, data: bytes) -> None:
    """Dispatch send to transport supporting send_report or write_report."""
    if hasattr(transport, "send_report"):
        transport.send_report(report_id, data)
    else:
        transport.write_report(data, report_id=report_id)


def _receive(transport: HidTransport, timeout_s: float) -> bytes:
    """Dispatch receive to transport supporting receive_report or read_report."""
    if hasattr(transport, "receive_report"):
        return transport.receive_report(timeout=timeout_s)
    resp = transport.read_report(timeout_ms=max(1, int(timeout_s * 1000)))
    if resp is None:
        raise TimeoutError(f"HID read timed out after {timeout_s:.2f}s")
    return resp


def write_remap_image(
    transport: HidTransport,
    image: bytes | bytearray,
    baseline: Optional[bytes | bytearray] = None,
    policy: Optional[RemapWritePolicy] = None,
) -> RemapWriteResult:
    """
    Production-ready closed-loop WRITE transaction for Remap L1 (AA 22).

    10-step algorithm:
    1. Read baseline via AA 12 if not provided.
    2. Validate image & baseline dimensions (512 bytes).
    3. Calculate exact diff: baseline -> target.
    4. If diff is empty: early exit with 0 writes.
    5. Read-before-write verification: verify device still matches baseline.
    6. Send 10 chunks strictly sequentially with ACK validation on each.
    7. Read-back full 512-byte table via AA 12.
    8. Compare read-back byte-for-byte with target image.
    9. On mismatch: raise / report failure.
    10. Return structured RemapWriteResult.
    """
    if policy is None:
        policy = RemapWritePolicy(confirmed=False)

    # Safety Gate
    if policy.require_confirmation and not policy.confirmed:
        raise PermissionError(
            "Remap write rejected: safety policy requires explicit confirmation (confirmed=True)"
        )

    timeout_s = max(0.1, policy.timeout_ms / 1000.0)
    device_baseline: Optional[bytes] = None

    # STEP 1: Read baseline if not provided
    if baseline is None:
        device_baseline = read_keymap_table(
            transport, opcode=REMAP_READ_OPCODE, timeout=timeout_s
        )
        baseline = device_baseline
    else:
        baseline = bytes(baseline)

    # STEP 2: Validate dimensions
    if len(image) != REMAP_IMAGE_SIZE:
        raise ValueError(
            f"Target image must be exactly {REMAP_IMAGE_SIZE} bytes, got {len(image)}"
        )
    if len(baseline) != REMAP_IMAGE_SIZE:
        raise ValueError(
            f"Baseline image must be exactly {REMAP_IMAGE_SIZE} bytes, got {len(baseline)}"
        )
    target_bytes = bytes(image)

    # STEP 3: Exact diff
    diff = diff_images(baseline, target_bytes)
    changed_slots = sorted(list(set(d.slot for d in diff)))

    # STEP 4: Empty diff early-out
    if not diff:
        return RemapWriteResult(
            success=True,
            chunks_sent=0,
            acks_received=0,
            changed_bytes=[],
            changed_slots=[],
            readback_verified=True,
            baseline_image=baseline,
            written_image=target_bytes,
            readback_image=baseline,
            summary="No differences between baseline and target image. Zero WRITE packets sent.",
        )

    # STEP 5: Verify device state matches baseline before writing
    if device_baseline is None:
        device_baseline = read_keymap_table(
            transport, opcode=REMAP_READ_OPCODE, timeout=timeout_s
        )

    if device_baseline != baseline:
        mismatch_diff = diff_images(baseline, device_baseline)
        raise RuntimeError(
            f"Read-before-write mismatch: device state does not match expected baseline "
            f"({len(mismatch_diff)} bytes differ)"
        )

    # STEP 6: Send 10 chunks strictly sequentially
    chunks = build_remap_write_chunks(target_bytes)
    chunks_sent = 0
    acks_received = 0

    for idx, (addr, payload, packet) in enumerate(chunks):
        # Send chunk
        try:
            _send(transport, 0, packet)
            chunks_sent += 1
        except Exception as e:
            raise RuntimeError(
                f"Failed to send chunk #{idx} (addr 0x{addr:04X}): {e}"
            ) from e

        # Receive ACK
        try:
            ack_report = _receive(transport, timeout_s=timeout_s)
        except Exception as e:
            raise TimeoutError(
                f"Timeout waiting for ACK on chunk #{idx} (addr 0x{addr:04X}): {e}"
            ) from e

        # Validate ACK
        validate_remap_ack(
            expected_address=addr,
            expected_payload=payload,
            response=ack_report,
        )
        acks_received += 1

    # STEP 7: Read-back full table via AA 12
    readback_image = read_keymap_table(
        transport, opcode=REMAP_READ_OPCODE, timeout=timeout_s
    )

    # STEP 8: Byte-for-byte compare
    readback_diffs = diff_images(target_bytes, readback_image)

    # STEP 9: Check readback
    if readback_diffs:
        diff_desc = ", ".join(
            f"offset 0x{d.offset:04X}: expected 0x{d.before:02X}, got 0x{d.after:02X}"
            for d in readback_diffs[:5]
        )
        err_msg = (
            f"Read-back verification FAILURE: {len(readback_diffs)} byte(s) mismatch ({diff_desc})"
        )
        if policy.require_readback:
            raise RuntimeError(err_msg)
        return RemapWriteResult(
            success=False,
            chunks_sent=chunks_sent,
            acks_received=acks_received,
            changed_bytes=diff,
            changed_slots=changed_slots,
            readback_verified=False,
            baseline_image=baseline,
            written_image=target_bytes,
            readback_image=readback_image,
            error_message=err_msg,
        )

    # STEP 10: Success
    return RemapWriteResult(
        success=True,
        chunks_sent=chunks_sent,
        acks_received=acks_received,
        changed_bytes=diff,
        changed_slots=changed_slots,
        readback_verified=True,
        baseline_image=baseline,
        written_image=target_bytes,
        readback_image=readback_image,
        summary=(
            f"Successfully wrote and verified Remap L1: {chunks_sent} chunks sent, "
            f"{acks_received} ACKs validated, {len(diff)} byte(s) modified in {len(changed_slots)} slot(s)."
        ),
    )
