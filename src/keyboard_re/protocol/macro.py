"""
Macro Subsystem (AA 15 / AA 25) Protocol Models, Parsers, and Serializers for Type 84.

Protocol Architecture:
- Read Opcode: AA 15 (HOST -> DEVICE), returns 55 15 reports.
  * Catalog: 400 bytes at address 0x0000 (100 slots x 4 bytes uint32_le offset pointers).
  * 8 chunks: 7 x 56 bytes + 1 x 8 bytes tail at address 0x0188 (392).
  * Action Heap: starts at address 400 (0x0190).
  * Action Body: 4-byte header [f & 0xFF, (f >> 8) & 0xFF, 0x00, 0x00] where f = k * 2 (k = action count).
  * Followed by k x 4-byte actions: [delay uint16_le, keyCode uint8, flags uint8].
  * Flags: bit 7 = isPress (1=Press, 0=Release), bits 4..6 = actionType (1=Keyboard, 3=Mouse).
- Write Opcode: AA 25 (HOST -> DEVICE), ACK: 55 25 reports.
  * Two sequential stages (no separate commit opcode):
    1. Catalog: 400 bytes at address 0x0000 (8 chunks: 7 x 56B + 1 x 8B).
    2. Heap (if len(heap) > 0): starting at address 0x0190 (400) in 56-byte chunks.
    Final chunk sets isLastPacket flag (pkt[6] = 0x01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import math
from pathlib import Path
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.plan import WriteChunk, build_chunk_packet, build_expected_ack
from keyboard_re.protocol.transport import HidTransport

MACRO_BUFFER_SIZE: int = 400
MACRO_SLOT_COUNT: int = 100
MACRO_CHUNK_COUNT: int = 8
MACRO_CHUNK_SIZE: int = 56
MACRO_TAIL_SIZE: int = 8
MACRO_TAIL_ADDR: int = 0x0188  # 392
MACRO_HEAP_START: int = 400

MACRO_READ_OPCODE: int = 0x15
MACRO_WRITE_OPCODE: int = 0x25


class MacroActionType(IntEnum):
    """Macro action event category."""
    KEYBOARD = 1
    KEYBOARD_EXT = 2
    MOUSE = 3


@dataclass
class MacroAction:
    """
    Single atomic macro action event (4 bytes on wire).

    Wire Format:
    - Bytes 0..1: delay in milliseconds (uint16_le, 0..65535)
    - Byte 2: HID keycode (uint8)
    - Byte 3: flags (bit 7: isPress, bits 4..6: actionType)
    """
    action_type: int = 1     # 1 = Keyboard, 3 = Mouse
    is_press: bool = True    # True = Down / Press, False = Up / Release
    key_code: int = 0        # HID keycode (e.g. 0x04 for 'A')
    delay: int = 0           # ms delay before this event (0..65535)

    def to_bytes(self) -> bytes:
        """Serialize action to canonical 4-byte wire representation."""
        flags = (0x80 if self.is_press else 0x00) | ((self.action_type & 0x07) << 4)
        return struct.pack("<HBB", min(65535, max(0, int(self.delay))), int(self.key_code) & 0xFF, flags)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> MacroAction:
        """Deserialize action from 4-byte buffer."""
        if len(data) < 4:
            raise ValueError(f"MacroAction requires at least 4 bytes, got {len(data)}")
        delay, key_code, flags = struct.unpack("<HBB", bytes(data[:4]))
        is_press = bool(flags & 0x80)
        action_type = (flags >> 4) & 0x07
        return cls(action_type=action_type, is_press=is_press, key_code=key_code, delay=delay)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "is_press": self.is_press,
            "key_code": self.key_code,
            "delay": self.delay,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MacroAction:
        return cls(
            action_type=data.get("action_type", 1),
            is_press=data.get("is_press", True),
            key_code=data.get("key_code", 0),
            delay=data.get("delay", 0),
        )

    def clone(self) -> MacroAction:
        return MacroAction(
            action_type=self.action_type,
            is_press=self.is_press,
            key_code=self.key_code,
            delay=self.delay,
        )


@dataclass
class MacroDefinition:
    """
    Structured definition of a single user macro.
    """
    macro_id: int                    # 0..99
    name: str = ""                   # Host-side name label
    actions: List[MacroAction] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.actions)

    def to_body_bytes(self) -> bytes:
        """
        Serialize macro actions with 4-byte header into action heap format:
        - Header (4B): [f & 0xFF, (f >> 8) & 0xFF, 0x00, 0x00] where f = len(actions) * 2
        - Body: len(actions) * 4 bytes of MacroAction wire records
        """
        k = len(self.actions)
        f = k * 2
        buf = bytearray(4 + k * 4)
        buf[0] = f & 0xFF
        buf[1] = (f >> 8) & 0xFF
        buf[2] = 0x00
        buf[3] = 0x00
        for i, act in enumerate(self.actions):
            buf[4 + i * 4 : 4 + (i + 1) * 4] = act.to_bytes()
        return bytes(buf)

    @classmethod
    def from_body_bytes(cls, macro_id: int, data: bytes | bytearray, name: str = "") -> MacroDefinition:
        """Deserialize macro definition from body binary data."""
        if len(data) < 4:
            return cls(macro_id=macro_id, name=name, actions=[])
        f = data[0] | (data[1] << 8)
        k = f // 2
        actions = []
        for i in range(k):
            offset = 4 + i * 4
            if offset + 4 <= len(data):
                actions.append(MacroAction.from_bytes(data[offset : offset + 4]))
        return cls(macro_id=macro_id, name=name, actions=actions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "macro_id": self.macro_id,
            "name": self.name,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MacroDefinition:
        return cls(
            macro_id=data.get("macro_id", 0),
            name=data.get("name", ""),
            actions=[MacroAction.from_dict(a) for a in data.get("actions", [])],
        )

    def clone(self) -> MacroDefinition:
        return MacroDefinition(
            macro_id=self.macro_id,
            name=self.name,
            actions=[a.clone() for a in self.actions],
        )


@dataclass
class MacroCatalog:
    """
    Catalog of up to 100 macros (IDs 0..99) with action heap serialization.
    """
    macros: Dict[int, MacroDefinition] = field(default_factory=dict)

    def get_macro(self, macro_id: int) -> Optional[MacroDefinition]:
        return self.macros.get(macro_id)

    def set_macro(self, macro: MacroDefinition) -> None:
        if macro.macro_id < 0 or macro.macro_id >= MACRO_SLOT_COUNT:
            raise ValueError(f"macro_id must be in 0..{MACRO_SLOT_COUNT - 1}, got {macro.macro_id}")
        self.macros[macro.macro_id] = macro

    def delete_macro(self, macro_id: int) -> bool:
        if macro_id in self.macros:
            del self.macros[macro_id]
            return True
        return False

    def build_image(self) -> Tuple[bytes, bytes]:
        """
        Build (catalog_400b, heap_bytes) tuple:
        - catalog_400b: exactly 400 bytes (100 x 4 bytes uint32_le pointer).
        - heap_bytes: concatenated macro action bodies starting at address 400.
        """
        catalog = bytearray(MACRO_BUFFER_SIZE)
        heap = bytearray()
        current_offset = MACRO_HEAP_START

        for macro_id in sorted(self.macros.keys()):
            macro = self.macros[macro_id]
            body = macro.to_body_bytes()
            struct.pack_into("<I", catalog, macro_id * 4, current_offset)
            heap.extend(body)
            current_offset += len(body)

        return bytes(catalog), bytes(heap)

    def to_full_image(self) -> bytes:
        """
        Return combined binary image (400-byte catalog + heap bytes).
        If no macros exist, returns 400 bytes of zeros.
        """
        cat, heap = self.build_image()
        return cat + heap

    @classmethod
    def from_full_image(
        cls,
        data: bytes | bytearray,
        names: Optional[Dict[int, str]] = None,
    ) -> MacroCatalog:
        """Reconstruct MacroCatalog from combined binary image."""
        if len(data) < MACRO_BUFFER_SIZE:
            return cls()

        catalog = bytes(data[:MACRO_BUFFER_SIZE])
        names = names or {}
        macros: Dict[int, MacroDefinition] = {}

        for slot in range(MACRO_SLOT_COUNT):
            ptr = struct.unpack_from("<I", catalog, slot * 4)[0]
            if ptr >= MACRO_HEAP_START and ptr < len(data):
                body_data = data[ptr:]
                name = names.get(slot, f"Macro {slot}")
                macro_def = MacroDefinition.from_body_bytes(macro_id=slot, data=body_data, name=name)
                macros[slot] = macro_def

        return cls(macros=macros)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "macros": {str(mid): m.to_dict() for mid, m in self.macros.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MacroCatalog:
        cat = cls()
        m_dict = data.get("macros", {})
        for mid_str, def_data in m_dict.items():
            try:
                mid = int(mid_str)
                cat.macros[mid] = MacroDefinition.from_dict(def_data)
            except Exception:
                pass
        return cat

    def clone(self) -> MacroCatalog:
        return MacroCatalog(macros={mid: m.clone() for mid, m in self.macros.items()})


# -----------------------------------------------------------------------------
# Backwards Compatibility: MacroTable & parse_macro_chunks
# -----------------------------------------------------------------------------

@dataclass
class MacroTable:
    """
    Reconstructed 400-byte Macro configuration catalog.
    Maintained for backwards compatibility.
    """
    raw_bytes: bytes
    has_user_macros: bool
    version_flag: int = 1

    @property
    def total_bytes(self) -> int:
        return len(self.raw_bytes)


def parse_macro_chunks(reports: Sequence[bytes | bytearray]) -> MacroTable:
    """
    Parse a sequence of 8 incoming chunks (55 15 ...) into a MacroTable.
    """
    if len(reports) != MACRO_CHUNK_COUNT:
        raise ValueError(f"Expected {MACRO_CHUNK_COUNT} reports for macro read, got {len(reports)}")

    buf = bytearray(MACRO_BUFFER_SIZE)

    # 1. First 7 chunks (56 bytes each at offsets 0, 56, 112, ...)
    for i in range(7):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if not rep.startswith(b"\x55\x15\x38"):
            raise ValueError(f"Report #{i}: expected prefix 55 15 38, got {rep[:3].hex().upper()}")
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * MACRO_CHUNK_SIZE
        if addr != expected_addr:
            raise ValueError(f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}")
        buf[addr : addr + MACRO_CHUNK_SIZE] = rep[8 : 8 + MACRO_CHUNK_SIZE]

    # 2. Final tail chunk (8 bytes at 0x0188 = 392)
    tail = bytes(reports[7])
    validate_report_size(tail, REPORT_SIZE)
    if not tail.startswith(b"\x55\x15\x08"):
        raise ValueError(f"Report #7: expected tail prefix 55 15 08, got {tail[:3].hex().upper()}")
    tail_addr = struct.unpack_from("<H", tail, 3)[0]
    if tail_addr != MACRO_TAIL_ADDR:
        raise ValueError(f"Report #7: tail address mismatch, expected 0x0188, got 0x{tail_addr:04X}")
    buf[MACRO_TAIL_ADDR : MACRO_TAIL_ADDR + MACRO_TAIL_SIZE] = tail[8 : 8 + MACRO_TAIL_SIZE]

    # Any non-zero pointer indicates user macros exist
    has_user_macros = any(b != 0 for b in buf)
    version_flag = tail[6] if len(tail) > 6 else 1

    return MacroTable(
        raw_bytes=bytes(buf),
        has_user_macros=has_user_macros,
        version_flag=version_flag,
    )


# -----------------------------------------------------------------------------
# Write Chunk Builder (AA 25)
# -----------------------------------------------------------------------------

def build_macro_write_chunks(image_or_catalog: bytes) -> List[WriteChunk]:
    """
    Build sequential AA 25 write chunks for macro configuration:
    - Stage 1: 400-byte catalog at address 0x0000 (8 chunks: 7 x 56B + 1 x 8B tail).
    - Stage 2 (if len > 400): heap starting at address 0x0190 (400) in 56B chunks.
    - Tail chunk of the entire sequence sets is_last=True.
    """
    if len(image_or_catalog) < MACRO_BUFFER_SIZE:
        raise ValueError(
            f"Macro buffer must be at least {MACRO_BUFFER_SIZE} bytes, got {len(image_or_catalog)}"
        )

    chunks: List[WriteChunk] = []
    catalog = image_or_catalog[:MACRO_BUFFER_SIZE]
    heap = image_or_catalog[MACRO_BUFFER_SIZE:]
    has_heap = len(heap) > 0

    # 1. Catalog Chunks (addresses 0, 56, 112, 168, 224, 280, 336)
    for i in range(7):
        addr = i * MACRO_CHUNK_SIZE
        payload = catalog[addr : addr + MACRO_CHUNK_SIZE]
        pkt = build_chunk_packet(MACRO_WRITE_OPCODE, MACRO_CHUNK_SIZE, addr, payload, is_last=False)
        ack = build_expected_ack(MACRO_WRITE_OPCODE, MACRO_CHUNK_SIZE, addr, payload, is_last=False)
        chunks.append(WriteChunk(chunk_index=len(chunks), address=addr, size=MACRO_CHUNK_SIZE, packet=pkt, expected_ack=ack))

    # 2. Catalog Tail Chunk (8 bytes at address 392)
    tail_payload = catalog[MACRO_TAIL_ADDR : MACRO_TAIL_ADDR + MACRO_TAIL_SIZE]
    catalog_tail_is_last = not has_heap
    pkt = build_chunk_packet(MACRO_WRITE_OPCODE, MACRO_TAIL_SIZE, MACRO_TAIL_ADDR, tail_payload, is_last=catalog_tail_is_last)
    ack = build_expected_ack(MACRO_WRITE_OPCODE, MACRO_TAIL_SIZE, MACRO_TAIL_ADDR, tail_payload, is_last=catalog_tail_is_last)
    chunks.append(WriteChunk(chunk_index=len(chunks), address=MACRO_TAIL_ADDR, size=MACRO_TAIL_SIZE, packet=pkt, expected_ack=ack))

    # 3. Heap Chunks (starting at address 400)
    if has_heap:
        m = len(heap)
        heap_chunk_count = math.ceil(m / MACRO_CHUNK_SIZE)
        for j in range(heap_chunk_count):
            addr = MACRO_HEAP_START + j * MACRO_CHUNK_SIZE
            rem = m - j * MACRO_CHUNK_SIZE
            size = min(MACRO_CHUNK_SIZE, rem)
            is_last = (j == heap_chunk_count - 1)
            payload = heap[j * MACRO_CHUNK_SIZE : j * MACRO_CHUNK_SIZE + size]

            pkt = build_chunk_packet(MACRO_WRITE_OPCODE, size, addr, payload, is_last=is_last)
            ack = build_expected_ack(MACRO_WRITE_OPCODE, size, addr, payload, is_last=is_last)
            chunks.append(WriteChunk(chunk_index=len(chunks), address=addr, size=size, packet=pkt, expected_ack=ack))

    return chunks


# -----------------------------------------------------------------------------
# Hardware I/O Read / Write Helpers
# -----------------------------------------------------------------------------

def read_macro_catalog_raw(transport: HidTransport, timeout: float = 1.0) -> bytes:
    """Read the 400-byte macro catalog buffer via AA 15."""
    buf = bytearray(MACRO_BUFFER_SIZE)

    for i in range(7):
        addr = i * MACRO_CHUNK_SIZE
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = MACRO_READ_OPCODE
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)

        validate_report_size(resp, REPORT_SIZE)
        if resp[0] != 0x55 or resp[1] != MACRO_READ_OPCODE or resp[2] != 0x38:
            raise ValueError(f"Macro chunk #{i}: expected prefix 55 15 38, got {resp[:3].hex(' ').upper()}")
        resp_addr = struct.unpack_from("<H", resp, 3)[0]
        if resp_addr != addr:
            raise ValueError(f"Macro chunk #{i}: address mismatch: expected 0x{addr:04X}, got 0x{resp_addr:04X}")
        buf[addr : addr + MACRO_CHUNK_SIZE] = resp[8 : 8 + MACRO_CHUNK_SIZE]

    req_tail = bytearray(REPORT_SIZE)
    req_tail[0] = 0xAA
    req_tail[1] = MACRO_READ_OPCODE
    req_tail[2] = 0x08
    struct.pack_into("<H", req_tail, 3, MACRO_TAIL_ADDR)
    req_tail[6] = 0x01
    transport.send_report(0, bytes(req_tail))
    resp_tail = transport.receive_report(timeout=timeout)

    validate_report_size(resp_tail, REPORT_SIZE)
    if resp_tail[0] != 0x55 or resp_tail[1] != MACRO_READ_OPCODE or resp_tail[2] != 0x08:
        raise ValueError(f"Macro tail chunk: expected prefix 55 15 08, got {resp_tail[:3].hex(' ').upper()}")
    tail_addr = struct.unpack_from("<H", resp_tail, 3)[0]
    if tail_addr != MACRO_TAIL_ADDR:
        raise ValueError(f"Macro tail chunk: address mismatch: expected 0x0188, got 0x{tail_addr:04X}")
    buf[MACRO_TAIL_ADDR : MACRO_TAIL_ADDR + MACRO_TAIL_SIZE] = resp_tail[8 : 8 + MACRO_TAIL_SIZE]

    return bytes(buf)


def read_macro_full(transport: HidTransport, timeout: float = 1.0) -> MacroCatalog:
    """
    Read full macro configuration from physical keyboard:
    1. Reads 400-byte catalog.
    2. Identifies all non-zero pointers.
    3. Reads corresponding action bodies from heap.
    """
    catalog_bytes = read_macro_catalog_raw(transport, timeout=timeout)
    catalog = MacroCatalog()

    for slot in range(MACRO_SLOT_COUNT):
        ptr = struct.unpack_from("<I", catalog_bytes, slot * 4)[0]
        if ptr < MACRO_HEAP_START:
            continue

        # Read 4-byte header at ptr
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = MACRO_READ_OPCODE
        req[2] = 0x04
        struct.pack_into("<H", req, 3, ptr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)

        header_f = resp[8] | (resp[9] << 8)
        action_count = header_f // 2
        if action_count <= 0:
            continue

        # Read action_count * 4 bytes starting at ptr + 4
        total_action_bytes = action_count * 4
        action_data = bytearray(total_action_bytes)
        read_offset = 0

        while read_offset < total_action_bytes:
            rem = total_action_bytes - read_offset
            chunk_sz = min(56, rem)
            curr_addr = ptr + 4 + read_offset

            req_act = bytearray(REPORT_SIZE)
            req_act[0] = 0xAA
            req_act[1] = MACRO_READ_OPCODE
            req_act[2] = chunk_sz
            struct.pack_into("<H", req_act, 3, curr_addr)
            transport.send_report(0, bytes(req_act))
            resp_act = transport.receive_report(timeout=timeout)

            action_data[read_offset : read_offset + chunk_sz] = resp_act[8 : 8 + chunk_sz]
            read_offset += chunk_sz

        full_body = bytes([resp[8], resp[9], resp[10], resp[11]]) + bytes(action_data)
        macro_def = MacroDefinition.from_body_bytes(macro_id=slot, data=full_body, name=f"Macro {slot}")
        catalog.set_macro(macro_def)

    return catalog


def write_macro_image(
    transport: HidTransport,
    image_bytes: bytes,
    timeout: float = 1.0,
) -> int:
    """
    Transmit AA 25 write chunks to physical keyboard and validate ACKs synchronously.
    Returns the number of reports sent.
    """
    chunks = build_macro_write_chunks(image_bytes)
    for idx, c in enumerate(chunks):
        transport.send_report(0, c.packet)
        ack = transport.receive_report(timeout=timeout)
        validate_report_size(ack, REPORT_SIZE)
        if ack[0] != 0x55 or ack[1] != MACRO_WRITE_OPCODE:
            raise ValueError(
                f"Macro write chunk #{idx} failed: expected ACK prefix 55 25, got {ack[:2].hex(' ').upper()}"
            )
        ack_addr = struct.unpack_from("<H", ack, 3)[0]
        if ack_addr != c.address:
            raise ValueError(
                f"Macro write chunk #{idx} address mismatch: expected 0x{c.address:04X}, got 0x{ack_addr:04X}"
            )
    return len(chunks)
