"""
Assembler for reconstructing 1008-byte configuration images from parsed packets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from keyboard_re.models import (
    CHUNK_COUNT,
    CHUNK_PAYLOAD_SIZE,
    CONFIG_IMAGE_SIZE,
    ConfigImage,
    KeyConfig,
    PacketType,
    ParsedPacket,
    RawCapture,
)


@dataclass
class AssemblyResult:
    """Result of assembling packets into a 1008-byte configuration image."""
    success: bool
    image: Optional[ConfigImage] = None
    chunks_found: int = 0
    expected_chunks: int = CHUNK_COUNT
    missing_addresses: List[int] = field(default_factory=list)
    has_terminator: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def assemble_packets(packets: List[ParsedPacket]) -> AssemblyResult:
    """
    Assemble parsed packets into a 1008-byte ConfigImage.
    Validates completeness, addresses, and terminator packet.
    """
    chunks_by_addr: Dict[int, ParsedPacket] = {}
    has_terminator = False
    errors: List[str] = []
    warnings: List[str] = []

    for pkt in packets:
        if not pkt.is_valid:
            warnings.append(f"Report #{pkt.index}: invalid packet ({pkt.error_message})")
            continue

        if pkt.packet_type == PacketType.TERMINATOR:
            has_terminator = True
            if pkt.terminator_size != CONFIG_IMAGE_SIZE:
                warnings.append(
                    f"Report #{pkt.index}: terminator reported size {pkt.terminator_size}, expected {CONFIG_IMAGE_SIZE}"
                )
            continue

        if pkt.packet_type == PacketType.DATA_CHUNK:
            addr = pkt.address
            if addr is None:
                errors.append(f"Report #{pkt.index}: data chunk missing address")
                continue

            if addr in chunks_by_addr:
                prev = chunks_by_addr[addr]
                if prev.payload != pkt.payload:
                    errors.append(
                        f"Conflicting duplicate chunk at address 0x{addr:04X} (Reports #{prev.index} and #{pkt.index})"
                    )
                else:
                    warnings.append(
                        f"Identical duplicate chunk at address 0x{addr:04X} (Reports #{prev.index} and #{pkt.index})"
                    )
            chunks_by_addr[addr] = pkt

    # Check for expected addresses: 0x0000, 0x0038, ..., 0x03B8
    expected_addresses = [i * CHUNK_PAYLOAD_SIZE for i in range(CHUNK_COUNT)]
    missing = [addr for addr in expected_addresses if addr not in chunks_by_addr]

    if missing:
        missing_str = ", ".join(f"0x{a:04X}" for a in missing)
        errors.append(f"Missing {len(missing)} chunk(s) at addresses: {missing_str}")

    if not has_terminator:
        warnings.append("No final terminator packet (AA 27 10 ...) observed in packet sequence")

    if errors or len(chunks_by_addr) < CHUNK_COUNT:
        return AssemblyResult(
            success=False,
            image=None,
            chunks_found=len(chunks_by_addr),
            expected_chunks=CHUNK_COUNT,
            missing_addresses=missing,
            has_terminator=has_terminator,
            errors=errors,
            warnings=warnings
        )

    # Build 1008-byte monolithic buffer
    buffer = bytearray(CONFIG_IMAGE_SIZE)
    for addr in sorted(chunks_by_addr.keys()):
        pkt = chunks_by_addr[addr]
        buffer[addr : addr + CHUNK_PAYLOAD_SIZE] = pkt.payload

    return AssemblyResult(
        success=True,
        image=ConfigImage(buffer),
        chunks_found=len(chunks_by_addr),
        expected_chunks=CHUNK_COUNT,
        missing_addresses=[],
        has_terminator=has_terminator,
        errors=errors,
        warnings=warnings
    )


def load_image_from_file_or_capture(path: Path | str, session_index: int = -1) -> ConfigImage:
    """Load a 1008-byte ConfigImage directly from .bin or by assembling from a .json capture."""
    p = Path(path)
    if p.suffix.lower() == ".bin":
        return ConfigImage(p.read_bytes())

    capture = RawCapture.load_json(p)
    sessions = capture.extract_sessions()
    if len(sessions) > 1:
        chosen_reports = sessions[session_index]
        from keyboard_re.parser import parse_reports
        parsed = parse_reports(chosen_reports)
    else:
        from keyboard_re.parser import parse_capture
        parsed = parse_capture(capture)

    res = assemble_packets(parsed)
    if not res.success or res.image is None:
        raise ValueError(f"Failed to assemble image from {p}: {'; '.join(res.errors)}")
    return res.image


def extract_key_configs_from_image(image: ConfigImage) -> Dict[str, KeyConfig]:
    """Extract typed KeyConfig for all 84 physical keys from a 1008-byte ConfigImage."""
    return image.all_key_configs()


def build_image_from_key_configs(
    configs: Dict[str, KeyConfig],
    template: Optional[ConfigImage] = None,
) -> ConfigImage:
    """
    Build a 1008-byte ConfigImage by applying KeyConfig values to a template
    (or empty image if none provided).
    """
    img = ConfigImage(template.data) if template else ConfigImage.empty()
    for key_name, cfg in configs.items():
        img.set_key_config(key_name, cfg)
    return img

