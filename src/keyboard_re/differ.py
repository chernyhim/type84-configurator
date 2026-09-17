"""
Diff engine for comparing 1008-byte configuration images and identifying slot changes.
"""

from __future__ import annotations

from typing import Any, Dict, List

from keyboard_re.models import (
    CONFIG_IMAGE_SIZE,
    KNOWN_FIELDS,
    SLOT_COUNT,
    SLOT_SIZE,
    ConfigImage,
    DiffEntry,
)


def diff_images(
    base: ConfigImage,
    modified: ConfigImage,
) -> List[DiffEntry]:
    """
    Compare two 1008-byte images and return a list of DiffEntries for all changed bytes.
    Maps changed absolute addresses to 8-byte candidate slots and known fields.
    """
    diffs: List[DiffEntry] = []

    for addr in range(CONFIG_IMAGE_SIZE):
        b_old = base.get_byte(addr)
        b_new = modified.get_byte(addr)
        if b_old != b_new:
            slot_idx = addr // SLOT_SIZE
            slot_off = addr % SLOT_SIZE
            known = KNOWN_FIELDS.get(addr)

            diffs.append(
                DiffEntry(
                    absolute_address=addr,
                    slot_index=slot_idx,
                    slot_offset=slot_off,
                    old_val=b_old,
                    new_val=b_new,
                    known_field=known
                )
            )

    return diffs


def format_diff_table(diffs: List[DiffEntry], title: str = "Configuration Diff") -> str:
    """Format diff entries into a clean human-readable table."""
    if not diffs:
        return f"{title}: No differences found (images are identical).\n"

    lines = [
        f"=== {title} ===",
        f"Total changed bytes: {len(diffs)}",
        "-" * 96,
        f"{'Abs Addr':<12} {'Slot:Off':<10} {'Old (Hex/Dec)':<16} {'New (Hex/Dec)':<16} {'Delta':<8} {'Field / Key':<20} {'Confidence':<12}",
        "-" * 96,
    ]

    for d in diffs:
        addr_str = f"0x{d.absolute_address:04X} ({d.absolute_address:4d})"
        slot_str = f"S{d.slot_index:03d} : +{d.slot_offset}"
        old_str = f"{d.old_hex} ({d.old_val:3d})"
        new_str = f"{d.new_hex} ({d.new_val:3d})"
        delta_str = f"{d.delta:+d}"

        field_name = "-"
        confidence = "-"
        if d.known_field:
            kf = d.known_field
            k_name = f"Key '{kf.key_name}'" if kf.key_name else ""
            field_name = f"{k_name} {kf.name}".strip()
            confidence = f"[{kf.confidence.value}]"

        lines.append(
            f"{addr_str:<12} {slot_str:<10} {old_str:<16} {new_str:<16} {delta_str:<8} {field_name:<20} {confidence:<12}"
        )

    lines.append("-" * 96)
    return "\n".join(lines)


def diff_summary_by_slot(diffs: List[DiffEntry]) -> Dict[int, List[DiffEntry]]:
    """Group diffs by slot index."""
    grouped: Dict[int, List[DiffEntry]] = {}
    for d in diffs:
        grouped.setdefault(d.slot_index, []).append(d)
    return grouped


def diff_to_dict(diffs: List[DiffEntry]) -> List[Dict[str, Any]]:
    """Convert diffs to a machine-readable list of dicts."""
    results = []
    for d in diffs:
        item: Dict[str, Any] = {
            "absolute_address_hex": f"0x{d.absolute_address:04X}",
            "absolute_address_dec": d.absolute_address,
            "slot_index": d.slot_index,
            "slot_offset": d.slot_offset,
            "old_value_hex": d.old_hex,
            "old_value_dec": d.old_val,
            "new_value_hex": d.new_hex,
            "new_value_dec": d.new_val,
            "delta": d.delta,
        }
        if d.known_field:
            item["known_field"] = {
                "name": d.known_field.name,
                "key_name": d.known_field.key_name,
                "confidence": d.known_field.confidence.value,
                "description": d.known_field.description,
            }
        else:
            item["known_field"] = None
        results.append(item)
    return results
