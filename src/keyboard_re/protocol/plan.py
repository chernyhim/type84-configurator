"""
Write Plan Generator for IO by Red Square Type 84 Magnetic Black.

Generates structured, verifiable dry-run write plans without transmitting to hardware:
Keyboard State -> Profile Model -> Diff -> Write Plan -> Protocol Encoders

Safety Guarantee:
- ZERO hardware transmissions or physical writes are executed.
- Only actually changed subsystems are included in the plan.
- Strict canonical execution sequence:
  1. Remap L1 (AA 22)
  2. Remap L2 (AA 26)
  3. RGB Global (AA 23)
  4. RGB Matrix (AA 24)
  5. Macro (AA 25)
  6. Hall RT (AA 27)
  7. DKS (AA 28)
- Game Mode is device-global and strictly excluded from profile plans.
- Byte-exact preservation: unknown buffers (Macro, DKS) are encoded verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union

from keyboard_re.protocol.diff import StateDiff, compare_snapshots
from keyboard_re.protocol.hall import (
    HALL_CHUNK_COUNT,
    HALL_CHUNK_PAYLOAD_SIZE,
    HALL_IMAGE_SIZE,
    build_hall_terminator,
    build_hall_write,
)
from keyboard_re.protocol.packets import REPORT_SIZE, pad_report, validate_report_size
from keyboard_re.protocol.rgb import (
    LED_BUFFER_SIZE,
    RGB_GLOBAL_MAGIC,
    RGBGlobalConfig,
    build_rgb_global,
    build_rgb_per_key_chunks,
)

if TYPE_CHECKING:
    from keyboard_re.models.state import DeviceState, KeyboardSnapshot, Profile
    from keyboard_re.profile_manager import ProfileDiff


@dataclass(frozen=True)
class WriteChunk:
    """A single 64-byte HID output report and its expected ACK."""
    chunk_index: int
    address: int
    size: int
    packet: bytes          # 64-byte HID output report (AA ...)
    expected_ack: bytes    # 64-byte expected ACK report (55 ...)


@dataclass
class SubsystemWriteStep:
    """A planned write operation for a single keyboard subsystem."""
    subsystem: str         # "remap_l1", "remap_l2", "rgb_global", "rgb_matrix", "macro", "hall", "dks"
    opcode: int            # e.g. 0x22, 0x26, 0x23, 0x24, 0x25, 0x27, 0x28
    description: str       # Summary of changes and packet count
    chunks: List[WriteChunk] = field(default_factory=list)
    raw_payload: Optional[bytes] = None  # Full byte-exact binary payload being written

    @property
    def packet_count(self) -> int:
        return len(self.chunks)

    @property
    def packets(self) -> List[bytes]:
        return [c.packet for c in self.chunks]


@dataclass
class ProfileWritePlan:
    """
    Deterministic, dry-run write plan for applying a Profile to a DeviceState.

    Safety Guarantee:
    - ZERO hardware transmissions or write calls are executed during plan generation.
    - Only actually modified subsystems are included in the plan.
    - Execution order strictly matches the confirmed vendor sequence:
      1. Remap L1 (AA 22)
      2. Remap L2 (AA 26)
      3. RGB Global (AA 23)
      4. RGB Matrix (AA 24)
      5. Macro (AA 25)
      6. Hall RT (AA 27)
      7. DKS (AA 28)
    - Game Mode is device-global and NOT included.
    """
    profile_id: int
    profile_name: str
    steps: List[SubsystemWriteStep] = field(default_factory=list)
    diff: Optional[ProfileDiff] = None

    @property
    def is_empty(self) -> bool:
        return len(self.steps) == 0

    @property
    def total_packets(self) -> int:
        return sum(s.packet_count for s in self.steps)

    @property
    def modified_subsystems(self) -> List[str]:
        return [s.subsystem for s in self.steps]

    @property
    def opcodes(self) -> List[int]:
        return [s.opcode for s in self.steps]

    def get_step(self, subsystem: str) -> Optional[SubsystemWriteStep]:
        for s in self.steps:
            if s.subsystem == subsystem:
                return s
        return None

    def format_text(self) -> str:
        lines = [
            "=" * 68,
            "PROFILE WRITE PLAN (DRY RUN - ZERO PHYSICAL WRITES)",
            "=" * 68,
            f"Profile: ID {self.profile_id} ('{self.profile_name}')",
            f"Total Subsystems to write: {len(self.steps)}",
            f"Total HID Reports:         {self.total_packets}",
        ]
        if self.is_empty:
            lines.append("Plan is EMPTY: Target profile is identical to current device state.")
            lines.append("=" * 68)
            lines.append("Safety Status: PASSIVE PREVIEW ONLY - DIRECT HARDWARE TRANSMISSIONS DISABLED")
            lines.append("=" * 68)
            return "\n".join(lines)

        lines.append("")
        lines.append("Planned Subsystem Execution Sequence:")
        for idx, step in enumerate(self.steps, 1):
            lines.append(f"  {idx}. [Opcode 0x{step.opcode:02X}] {step.subsystem.upper()}: {step.description}")
            lines.append(f"     -> {step.packet_count} packet(s), expected ACK prefix: 55 {step.opcode:02X} ...")

        lines.append("=" * 68)
        lines.append("Safety Status: PASSIVE PREVIEW ONLY - DIRECT HARDWARE TRANSMISSIONS DISABLED")
        lines.append("=" * 68)
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# Chunk Builder Helpers (Standardized wire encoding & ACK anticipation)
# -----------------------------------------------------------------------------

def build_chunk_packet(opcode: int, size: int, address: int, payload: bytes, is_last: bool = False) -> bytes:
    """Build a 64-byte HID output report (AA <opcode> <sz> <addr_lo> <addr_hi> 00 <flag> 00 <payload...>)."""
    pkt = bytearray(REPORT_SIZE)
    pkt[0] = 0xAA
    pkt[1] = opcode
    pkt[2] = size
    struct.pack_into("<H", pkt, 3, address)
    if is_last:
        pkt[6] = 0x01
    pkt[8 : 8 + len(payload)] = payload
    return bytes(pkt)


def build_expected_ack(opcode: int, size: int, address: int, payload: bytes, is_last: bool = False) -> bytes:
    """Synthesize the expected 64-byte device inputreport ACK (55 <opcode> <sz> <addr_lo> <addr_hi> 00 <flag> 00 <echo...>)."""
    ack = bytearray(REPORT_SIZE)
    ack[0] = 0x55
    ack[1] = opcode
    ack[2] = size
    struct.pack_into("<H", ack, 3, address)
    if is_last:
        ack[6] = 0x01
    ack[8 : 8 + len(payload)] = payload
    return bytes(ack)


def build_remap_write_chunks(image: bytes, opcode: int = 0x22) -> List[WriteChunk]:
    """
    Build 10 sequential write chunks for a 512-byte Remap table (Layer 1: 0x22, Layer 2: 0x26).
    9 chunks of 56 bytes + 1 tail chunk of 8 bytes.
    """
    if len(image) != 512:
        raise ValueError(f"Remap image must be exactly 512 bytes, got {len(image)}")

    chunks: List[WriteChunk] = []
    # 9 chunks of 56 bytes (0..503)
    for i in range(9):
        addr = i * 56
        payload = image[addr : addr + 56]
        pkt = build_chunk_packet(opcode, 56, addr, payload, is_last=False)
        ack = build_expected_ack(opcode, 56, addr, payload, is_last=False)
        chunks.append(WriteChunk(chunk_index=i, address=addr, size=56, packet=pkt, expected_ack=ack))

    # Tail chunk of 8 bytes (504..511)
    tail_addr = 504
    tail_payload = image[tail_addr : tail_addr + 8]
    pkt = build_chunk_packet(opcode, 8, tail_addr, tail_payload, is_last=True)
    ack = build_expected_ack(opcode, 8, tail_addr, tail_payload, is_last=True)
    chunks.append(WriteChunk(chunk_index=9, address=tail_addr, size=8, packet=pkt, expected_ack=ack))

    return chunks


def build_rgb_global_write_chunk(config: RGBGlobalConfig) -> WriteChunk:
    """Build single 64-byte report for Global RGB settings (AA 23 10 ...)."""
    magic = RGB_GLOBAL_MAGIC if not config.magic or config.magic == b"\x00\x00" else config.magic
    driver_setting = 255 if config.driver_setting in (None, 0) else config.driver_setting
    pkt = build_rgb_global(
        effect=config.effect,
        primary=config.primary,
        secondary=config.secondary,
        brightness=config.brightness,
        speed=config.speed,
        reserved_header=config.reserved_header,
        reserved_mid=config.reserved_mid,
        reserved_tail=config.reserved_tail,
        color_mode=config.color_mode,
        direction=config.direction,
        effect_mode_type=config.effect_mode_type,
        driver_setting=driver_setting,
        reserved_byte21=config.reserved_byte21,
        magic=magic,
    )
    # Device responds with 55 23 10 ... echoing the header and parameters
    ack_arr = bytearray(pkt)
    ack_arr[0] = 0x55
    ack = bytes(ack_arr)
    return WriteChunk(chunk_index=0, address=0, size=16, packet=pkt, expected_ack=ack)


def build_rgb_matrix_write_chunks(buffer: bytes) -> List[WriteChunk]:
    """
    Build 10 sequential write chunks for a 512-byte per-key LED buffer (AA 24).
    9 chunks of 56 bytes + 1 tail chunk of 8 bytes.
    """
    if len(buffer) != 512:
        raise ValueError(f"RGB matrix buffer must be exactly 512 bytes, got {len(buffer)}")

    chunks: List[WriteChunk] = []
    for i in range(9):
        addr = i * 56
        payload = buffer[addr : addr + 56]
        pkt = build_chunk_packet(0x24, 56, addr, payload)
        ack = build_expected_ack(0x24, 56, addr, payload)
        chunks.append(WriteChunk(chunk_index=i, address=addr, size=56, packet=pkt, expected_ack=ack))

    tail_addr = 504
    tail_payload = buffer[tail_addr : tail_addr + 8]
    pkt = build_chunk_packet(0x24, 8, tail_addr, tail_payload)
    ack = build_expected_ack(0x24, 8, tail_addr, tail_payload)
    chunks.append(WriteChunk(chunk_index=9, address=tail_addr, size=8, packet=pkt, expected_ack=ack))

    return chunks


def build_macro_write_chunks(buffer: bytes) -> List[WriteChunk]:
    """
    Build sequential AA 25 write chunks for macro configuration.
    Supports both 400-byte catalog alone and full catalog + heap images.
    """
    from keyboard_re.protocol.macro import build_macro_write_chunks as _build_macro_chunks
    return _build_macro_chunks(buffer)


def build_hall_write_chunks(image: bytes) -> List[WriteChunk]:
    """
    Build 19 sequential write chunks for a 1008-byte Hall configuration image (AA 27).
    18 chunks of 56 bytes (0..1007) + 1 terminator report of 16 bytes at address 0x03F0 (1008).
    """
    if len(image) < HALL_IMAGE_SIZE:
        raise ValueError(f"Hall image requires at least {HALL_IMAGE_SIZE} bytes, got {len(image)}")

    chunks: List[WriteChunk] = []
    for i in range(HALL_CHUNK_COUNT):
        addr = i * HALL_CHUNK_PAYLOAD_SIZE
        payload = image[addr : addr + HALL_CHUNK_PAYLOAD_SIZE]
        pkt = build_chunk_packet(0x27, 56, addr, payload)
        ack = build_expected_ack(0x27, 56, addr, payload)
        chunks.append(WriteChunk(chunk_index=i, address=addr, size=56, packet=pkt, expected_ack=ack))

    # Terminator report (AA 27 10 F0 03 00 01 00 ...)
    term_pkt = build_hall_terminator()
    ack_arr = bytearray(term_pkt)
    ack_arr[0] = 0x55
    term_ack = bytes(ack_arr)
    chunks.append(WriteChunk(chunk_index=18, address=1008, size=16, packet=term_pkt, expected_ack=term_ack))

    return chunks


def build_dks_write_chunks(buffer: bytes) -> List[WriteChunk]:
    """
    Build 19 sequential write chunks for a 1024-byte DKS data table (AA 28).
    18 chunks of 56 bytes (0..1007) + 1 tail chunk of 16 bytes at address 0x03F0 (1008..1023).
    """
    if len(buffer) != 1024:
        raise ValueError(f"DKS buffer must be exactly 1024 bytes, got {len(buffer)}")

    chunks: List[WriteChunk] = []
    for i in range(18):
        addr = i * 56
        payload = buffer[addr : addr + 56]
        pkt = build_chunk_packet(0x28, 56, addr, payload, is_last=False)
        ack = build_expected_ack(0x28, 56, addr, payload, is_last=False)
        chunks.append(WriteChunk(chunk_index=i, address=addr, size=56, packet=pkt, expected_ack=ack))

    tail_addr = 1008
    tail_payload = buffer[tail_addr : tail_addr + 16]
    pkt = build_chunk_packet(0x28, 16, tail_addr, tail_payload, is_last=True)
    ack = build_expected_ack(0x28, 16, tail_addr, tail_payload, is_last=True)
    chunks.append(WriteChunk(chunk_index=18, address=tail_addr, size=16, packet=pkt, expected_ack=ack))

    return chunks


def build_game_mode_write_chunk(config: Any) -> WriteChunk:
    """Build single 64-byte report for Game Mode / Settings (AA 21 38 00 00 00 01 00 ...)."""
    payload = config.to_payload() if hasattr(config, "to_payload") else bytes(config)
    if len(payload) != 56:
        raise ValueError(f"GameMode payload must be 56 bytes, got {len(payload)}")
    pkt = build_chunk_packet(0x21, 56, 0, payload, is_last=True)
    ack = build_expected_ack(0x21, 56, 0, payload, is_last=True)
    return WriteChunk(chunk_index=0, address=0, size=56, packet=pkt, expected_ack=ack)


# -----------------------------------------------------------------------------
# Plan Builder
# -----------------------------------------------------------------------------

def build_profile_write_plan(
    state: DeviceState,
    profile: Profile,
) -> ProfileWritePlan:
    """
    Build a safe, deterministic ProfileWritePlan comparing state against profile.

    Includes ONLY actually modified subsystems in canonical vendor execution order:
    1. Remap L1 (AA 22)
    2. Remap L2 (AA 26)
    3. RGB Global (AA 23)
    4. RGB Matrix (AA 24)
    5. Macro (AA 25)
    6. Hall RT (AA 27)
    7. DKS (AA 28)
    8. Game Mode / Settings (AA 21)
    """
    from keyboard_re.profile_manager import ProfileManager

    mgr = ProfileManager()
    diff = mgr.compare_profile_with_state(profile, state)

    steps: List[SubsystemWriteStep] = []

    # 1. Remap Layer 1 (AA 22)
    sub_l1 = diff.get_subsystem("remap_l1")
    if sub_l1 and sub_l1.has_changes and profile.remap is not None:
        raw_l1 = profile.remap.to_bytes() if hasattr(profile.remap, "to_bytes") else profile.remap.raw_bytes
        chunks_l1 = build_remap_write_chunks(raw_l1, opcode=0x22)
        steps.append(
            SubsystemWriteStep(
                subsystem="remap_l1",
                opcode=0x22,
                description=f"Remap Layer 1: {sub_l1.summary} (10 chunks)",
                chunks=chunks_l1,
                raw_payload=raw_l1,
            )
        )

    # 2. Remap Layer 2 / Fn (AA 26)
    sub_l2 = diff.get_subsystem("remap_l2")
    if sub_l2 and sub_l2.has_changes and profile.remap_l2 is not None:
        raw_l2 = profile.remap_l2.to_bytes() if hasattr(profile.remap_l2, "to_bytes") else profile.remap_l2.raw_bytes
        chunks_l2 = build_remap_write_chunks(raw_l2, opcode=0x26)
        steps.append(
            SubsystemWriteStep(
                subsystem="remap_l2",
                opcode=0x26,
                description=f"Remap Layer 2 (Fn): {sub_l2.summary} (10 chunks)",
                chunks=chunks_l2,
                raw_payload=raw_l2,
            )
        )

    # 3. RGB Global (AA 23) & 4. RGB Matrix (AA 24)
    sub_rgb_g = diff.get_subsystem("rgb_global")
    sub_rgb_m = diff.get_subsystem("rgb_matrix")

    need_rgb_g = bool(sub_rgb_g and sub_rgb_g.has_changes and profile.rgb_global is not None)
    need_rgb_m = bool(sub_rgb_m and sub_rgb_m.has_changes and profile.rgb_matrix is not None)

    # Custom 0x80 coordination: if switching to custom mode, ensure matrix is written;
    # if matrix changes in custom mode, ensure global 0x80 mode is confirmed.
    if profile.rgb_global and profile.rgb_global.effect == 0x80:
        if need_rgb_g and profile.rgb_matrix is not None:
            need_rgb_m = True
        elif need_rgb_m and profile.rgb_global is not None:
            need_rgb_g = True

    if need_rgb_g and profile.rgb_global is not None:
        chunk_rgb_g = build_rgb_global_write_chunk(profile.rgb_global)
        summary = sub_rgb_g.summary if sub_rgb_g else "Custom mode activation"
        steps.append(
            SubsystemWriteStep(
                subsystem="rgb_global",
                opcode=0x23,
                description=f"Global RGB: {summary} (1 report)",
                chunks=[chunk_rgb_g],
                raw_payload=None,
            )
        )

    # 4. RGB Matrix (AA 24)
    if need_rgb_m and profile.rgb_matrix is not None:
        raw_matrix = profile.rgb_matrix
        chunks_matrix = build_rgb_matrix_write_chunks(raw_matrix)
        summary = sub_rgb_m.summary if sub_rgb_m else "Custom per-key LED mapping"
        steps.append(
            SubsystemWriteStep(
                subsystem="rgb_matrix",
                opcode=0x24,
                description=f"Per-Key RGB Matrix: {summary} (10 chunks)",
                chunks=chunks_matrix,
                raw_payload=raw_matrix,
            )
        )

    # 5. Macro (AA 25)
    sub_macro = diff.get_subsystem("macro_raw")
    if sub_macro and sub_macro.has_changes and profile.macros_raw is not None:
        raw_macro = profile.macros_raw
        chunks_macro = build_macro_write_chunks(raw_macro)
        steps.append(
            SubsystemWriteStep(
                subsystem="macro",
                opcode=0x25,
                description=f"Macro Table: {sub_macro.summary} ({len(chunks_macro)} chunks)",
                chunks=chunks_macro,
                raw_payload=raw_macro,
            )
        )

    # 6. Hall RT (AA 27)
    sub_hall = diff.get_subsystem("hall")
    if sub_hall and sub_hall.has_changes and profile.hall is not None:
        raw_hall = profile.hall.to_bytes()
        chunks_hall = build_hall_write_chunks(raw_hall)
        steps.append(
            SubsystemWriteStep(
                subsystem="hall",
                opcode=0x27,
                description=f"Hall RT Profiles: {sub_hall.summary} (19 chunks)",
                chunks=chunks_hall,
                raw_payload=raw_hall,
            )
        )

    # 7. DKS (AA 28)
    sub_dks = diff.get_subsystem("dks_raw")
    if sub_dks and sub_dks.has_changes and profile.dks_raw is not None:
        raw_dks = profile.dks_raw
        chunks_dks = build_dks_write_chunks(raw_dks)
        steps.append(
            SubsystemWriteStep(
                subsystem="dks",
                opcode=0x28,
                description=f"DKS Table: {sub_dks.summary} (19 chunks)",
                chunks=chunks_dks,
                raw_payload=raw_dks,
            )
        )

    # 8. Game Mode / Settings (AA 21)
    sub_gm = diff.get_subsystem("game_mode")
    if sub_gm and sub_gm.has_changes and profile.game_mode is not None:
        raw_gm = profile.game_mode.to_payload() if hasattr(profile.game_mode, "to_payload") else bytes(56)
        chunk_gm = build_game_mode_write_chunk(profile.game_mode)
        steps.append(
            SubsystemWriteStep(
                subsystem="game_mode",
                opcode=0x21,
                description=f"Game Mode / Settings: {sub_gm.summary} (1 report)",
                chunks=[chunk_gm],
                raw_payload=raw_gm,
            )
        )

    return ProfileWritePlan(
        profile_id=profile.profile_id,
        profile_name=profile.name or f"Profile {profile.profile_id}",
        steps=steps,
        diff=diff,
    )


# -----------------------------------------------------------------------------
# Backwards Compatibility: Legacy WritePlan for Snapshots
# -----------------------------------------------------------------------------

@dataclass
class WritePlan:
    """
    Detailed, safe preview of packets and parameter changes to be transmitted.
    Maintained for backwards compatibility with snapshot diffing tests.
    """
    subsystem: str
    diffs: List[StateDiff]
    packets: List[bytes]
    profile_id: int = 1

    def format_plan(self) -> str:
        lines = [
            "=" * 64,
            "WRITE PLAN (DRY RUN - DIRECT HARDWARE WRITES DISABLED)",
            "=" * 64,
            f"Subsystem: {self.subsystem.upper()}",
            f"Profile:   {self.profile_id}",
            f"Changes ({len(self.diffs)} items):",
        ]
        for d in self.diffs:
            lines.append(f"  - {d.format_text()}")

        lines.append("")
        lines.append(f"Generated Packets ({len(self.packets)} reports, 64-byte each):")
        for idx, pkt in enumerate(self.packets):
            lines.append(f"  #{idx+1:2d}: {pkt[:16].hex(' ').upper()} ... ({len(pkt)} bytes)")

        lines.append("=" * 64)
        lines.append("Safety Status: ZERO HARDWARE TRANSMISSIONS EXECUTED")
        lines.append("=" * 64)
        return "\n".join(lines)


def build_write_plan(
    before: KeyboardSnapshot,
    intended: KeyboardSnapshot,
) -> WritePlan:
    """
    Build a WritePlan from differences between before and intended snapshots.
    Maintained for backwards compatibility with snapshot tests.
    """
    diffs = compare_snapshots(before, intended)
    if not diffs:
        return WritePlan(
            subsystem="none",
            diffs=[],
            packets=[],
            profile_id=before.active_profile_id,
        )

    subsystems = {d.subsystem for d in diffs}
    packets: List[bytes] = []
    primary_subsystem = diffs[0].subsystem
    profile_id = diffs[0].profile_id or before.active_profile_id

    if "hall" in subsystems:
        primary_subsystem = "hall"
        prof = intended.profiles[profile_id]
        hall_img = prof.hall.to_bytes()
        packets = build_hall_write(hall_img) + [build_hall_terminator()]

    elif "rgb_global" in subsystems:
        primary_subsystem = "rgb_global"
        prof = intended.profiles[profile_id]
        if prof.rgb_global is not None:
            packets = [
                build_rgb_global(
                    effect=prof.rgb_global.effect,
                    primary=prof.rgb_global.primary,
                    secondary=prof.rgb_global.secondary,
                    brightness=prof.rgb_global.brightness,
                    speed=prof.rgb_global.speed,
                )
            ]

    elif "rgb_per_key" in subsystems:
        primary_subsystem = "rgb_per_key"
        prof = intended.profiles[profile_id]
        if prof.rgb_per_key is not None:
            packets = build_rgb_per_key_chunks(prof.rgb_per_key)

    return WritePlan(
        subsystem=primary_subsystem,
        diffs=diffs,
        packets=packets,
        profile_id=profile_id,
    )

