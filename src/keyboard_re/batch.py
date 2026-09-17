"""
Batch analyzer for experimental capture files.
Correlates multiple controlled GUI experiments to deduce:
key -> slot -> byte offset -> parameter -> confidence.

Strict rule: NO automatic conclusions about unknown bytes based on a single experiment.
Confidence only escalates to CONFIRMED with repeated, isolated cross-validation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from keyboard_re.assembler import load_image_from_file_or_capture
from keyboard_re.differ import diff_images
from keyboard_re.models import (
    ACTUATION_OFFSET,
    BANK_STRIDE,
    SLOT_COUNT,
    SLOT_SIZE,
    ConfidenceLevel,
    ConfigImage,
    DiffEntry,
    RawCapture,
)


@dataclass
class ExperimentSpec:
    """Specification of an isolated GUI experiment."""
    id: str
    base_capture: str
    experimental_capture: str
    key_name: str
    parameter_name: str
    old_value: Any
    new_value: Any
    description: str = ""
    base_session: int = 0
    exp_session: int = -1


@dataclass
class ExperimentOutcome:
    """Observed diff results for one experiment."""
    spec: ExperimentSpec
    diffs: List[DiffEntry] = field(default_factory=list)
    status: str = "PENDING"  # "ISOLATED_OK", "NOISY", "NO_CHANGE", "ERROR"
    error_message: Optional[str] = None
    detected_slot: Optional[int] = None
    detected_offset: Optional[int] = None
    detected_address: Optional[int] = None
    old_byte: Optional[int] = None
    new_byte: Optional[int] = None
    delta: Optional[int] = None


@dataclass
class KeySlotCorrelation:
    """Empirical correlation between a key name and an 8-byte slot index."""
    key_name: str
    slot_index: int
    bank_index: int
    column_index: int
    confidence: ConfidenceLevel
    experiment_ids: List[str] = field(default_factory=list)
    observations_count: int = 0
    notes: str = ""


@dataclass
class OffsetParamCorrelation:
    """Empirical correlation between a byte offset in slots and a configuration parameter."""
    byte_offset: int
    parameter_name: str
    confidence: ConfidenceLevel
    keys_tested: List[str] = field(default_factory=list)
    experiment_ids: List[str] = field(default_factory=list)
    observations_count: int = 0
    notes: str = ""


@dataclass
class CorrelationRecord:
    """Unified row: key -> slot -> byte offset -> parameter -> confidence."""
    key_name: str
    slot_index: int
    bank_index: int
    column_index: int
    byte_offset: int
    absolute_address: int
    parameter_name: str
    confidence: ConfidenceLevel
    supporting_experiments: List[str] = field(default_factory=list)
    notes: str = ""


class BatchAnalyzer:
    """
    Orchestrates batch execution of multiple capture experiments,
    cross-validates results, and derives correlation tables with strict confidence checks.
    """

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.experiments: List[ExperimentSpec] = []
        self.outcomes: List[ExperimentOutcome] = []
        self.key_correlations: Dict[str, KeySlotCorrelation] = {}
        self.offset_correlations: Dict[int, OffsetParamCorrelation] = {}
        self.full_correlations: List[CorrelationRecord] = []

    def add_experiment(self, spec: ExperimentSpec) -> None:
        self.experiments.append(spec)

    def load_config_json(self, config_path: Path | str) -> None:
        p = Path(config_path)
        if not p.is_absolute():
            p = self.workspace_root / p
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_experiments = data.get("experiments", [])
        for exp_data in raw_experiments:
            self.add_experiment(
                ExperimentSpec(
                    id=exp_data.get("id", f"exp_{len(self.experiments)+1}"),
                    base_capture=exp_data["base_capture"],
                    experimental_capture=exp_data["experimental_capture"],
                    key_name=exp_data["key_name"],
                    parameter_name=exp_data["parameter_name"],
                    old_value=exp_data.get("old_value"),
                    new_value=exp_data.get("new_value"),
                    description=exp_data.get("description", ""),
                    base_session=exp_data.get("base_session", 0),
                    exp_session=exp_data.get("exp_session", -1),
                )
            )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if p.is_absolute():
            return p
        return self.workspace_root / p

    def run_batch(self) -> List[ExperimentOutcome]:
        self.outcomes.clear()

        for spec in self.experiments:
            base_p = self._resolve_path(spec.base_capture)
            exp_p = self._resolve_path(spec.experimental_capture)

            if not base_p.exists():
                self.outcomes.append(
                    ExperimentOutcome(
                        spec=spec,
                        status="ERROR",
                        error_message=f"Base file not found: {base_p}",
                    )
                )
                continue

            if not exp_p.exists():
                self.outcomes.append(
                    ExperimentOutcome(
                        spec=spec,
                        status="ERROR",
                        error_message=f"Experimental file not found: {exp_p}",
                    )
                )
                continue

            try:
                # Handle single file with multiple internal sessions
                if base_p == exp_p:
                    cap = RawCapture.load_json(base_p)
                    sessions = cap.extract_sessions()
                    if len(sessions) < 2:
                        raise ValueError(f"Self-diff requires at least 2 sessions in {base_p.name}")
                    from keyboard_re.assembler import assemble_packets
                    from keyboard_re.parser import parse_reports
                    img1 = assemble_packets(parse_reports(sessions[spec.base_session])).image
                    img2 = assemble_packets(parse_reports(sessions[spec.exp_session])).image
                else:
                    img1 = load_image_from_file_or_capture(base_p, session_index=spec.base_session)
                    img2 = load_image_from_file_or_capture(exp_p, session_index=spec.exp_session)

                diffs = diff_images(img1, img2)

                outcome = ExperimentOutcome(spec=spec, diffs=diffs)

                if len(diffs) == 0:
                    outcome.status = "NO_CHANGE"
                elif len(diffs) == 1:
                    outcome.status = "ISOLATED_OK"
                    d = diffs[0]
                    outcome.detected_slot = d.slot_index
                    outcome.detected_offset = d.slot_offset
                    outcome.detected_address = d.absolute_address
                    outcome.old_byte = d.old_val
                    outcome.new_byte = d.new_val
                    outcome.delta = d.delta
                else:
                    # Multi-byte change: noisy or composite setting
                    outcome.status = "NOISY"
                    outcome.detected_address = diffs[0].absolute_address
                    outcome.detected_slot = diffs[0].slot_index
                    outcome.detected_offset = diffs[0].slot_offset

                self.outcomes.append(outcome)

            except Exception as e:
                self.outcomes.append(
                    ExperimentOutcome(
                        spec=spec,
                        status="ERROR",
                        error_message=str(e),
                    )
                )

        self._evaluate_correlations()
        return self.outcomes

    def _evaluate_correlations(self) -> None:
        """
        Derive key->slot and offset->parameter correlations.
        STRICT CONFIDENCE RULES:
        - 1 isolated observation: PROBABLE (never CONFIRMED on a single test).
        - >= 2 consistent, independent observations: CONFIRMED.
        - 0 observations or noisy: UNKNOWN.
        """
        self.key_correlations.clear()
        self.offset_correlations.clear()
        self.full_correlations.clear()

        # Group isolated outcomes by key_name and by (param, offset)
        key_slots_map: Dict[str, Dict[int, List[ExperimentOutcome]]] = {}
        offset_params_map: Dict[int, Dict[str, List[ExperimentOutcome]]] = {}

        for out in self.outcomes:
            if out.status != "ISOLATED_OK":
                continue

            k_name = out.spec.key_name.strip().upper()
            param = out.spec.parameter_name.strip()
            slot = out.detected_slot
            off = out.detected_offset

            if slot is not None:
                key_slots_map.setdefault(k_name, {}).setdefault(slot, []).append(out)
            if off is not None:
                offset_params_map.setdefault(off, {}).setdefault(param, []).append(out)

        # 1. Evaluate Key -> Slot correlations
        for k_name, slots in key_slots_map.items():
            for slot_idx, outcomes in slots.items():
                count = len(outcomes)
                exp_ids = [o.spec.id for o in outcomes]
                bank = slot_idx // BANK_STRIDE
                col = slot_idx % BANK_STRIDE

                # Strictly require >= 2 independent experiments for CONFIRMED
                if count >= 2:
                    conf = ConfidenceLevel.CONFIRMED
                    note = f"Verified by {count} independent experiments ({', '.join(exp_ids)})"
                elif count == 1:
                    conf = ConfidenceLevel.PROBABLE
                    note = f"Observed in 1 experiment ({exp_ids[0]}). Requires repeat verification"
                else:
                    conf = ConfidenceLevel.UNKNOWN
                    note = "Insufficient data"

                self.key_correlations[k_name] = KeySlotCorrelation(
                    key_name=k_name,
                    slot_index=slot_idx,
                    bank_index=bank,
                    column_index=col,
                    confidence=conf,
                    experiment_ids=exp_ids,
                    observations_count=count,
                    notes=note,
                )

        # 2. Evaluate Offset -> Parameter correlations
        for off, params in offset_params_map.items():
            for param_name, outcomes in params.items():
                distinct_keys = sorted(list(set(o.spec.key_name.strip().upper() for o in outcomes)))
                count = len(outcomes)
                exp_ids = [o.spec.id for o in outcomes]

                # If confirmed across >= 2 different keys or repeated tests
                if len(distinct_keys) >= 2 or count >= 2:
                    conf = ConfidenceLevel.CONFIRMED
                    note = f"Confirmed across keys: {', '.join(distinct_keys)} ({count} experiments)"
                elif count == 1:
                    conf = ConfidenceLevel.PROBABLE
                    note = f"Observed on single key '{distinct_keys[0]}'. Needs cross-key validation"
                else:
                    conf = ConfidenceLevel.UNKNOWN
                    note = "Unverified"

                self.offset_correlations[off] = OffsetParamCorrelation(
                    byte_offset=off,
                    parameter_name=param_name,
                    confidence=conf,
                    keys_tested=distinct_keys,
                    experiment_ids=exp_ids,
                    observations_count=count,
                    notes=note,
                )

        # 3. Build unified correlation records: key -> slot -> byte offset -> parameter -> confidence
        for out in self.outcomes:
            if out.status != "ISOLATED_OK":
                continue

            k_name = out.spec.key_name.strip().upper()
            param = out.spec.parameter_name.strip()
            slot = out.detected_slot
            off = out.detected_offset
            addr = out.detected_address

            if slot is None or off is None or addr is None:
                continue

            k_corr = self.key_correlations.get(k_name)
            o_corr = self.offset_correlations.get(off)

            # Combined confidence is CONFIRMED only if BOTH key mapping and offset mapping are confirmed
            if k_corr and o_corr:
                if k_corr.confidence == ConfidenceLevel.CONFIRMED and o_corr.confidence == ConfidenceLevel.CONFIRMED:
                    comb_conf = ConfidenceLevel.CONFIRMED
                elif k_corr.confidence == ConfidenceLevel.UNKNOWN or o_corr.confidence == ConfidenceLevel.UNKNOWN:
                    comb_conf = ConfidenceLevel.UNKNOWN
                else:
                    comb_conf = ConfidenceLevel.PROBABLE
            else:
                comb_conf = ConfidenceLevel.PROBABLE

            # Avoid duplicates in list
            existing = next(
                (r for r in self.full_correlations if r.key_name == k_name and r.parameter_name == param),
                None
            )
            if existing:
                if out.spec.id not in existing.supporting_experiments:
                    existing.supporting_experiments.append(out.spec.id)
                existing.confidence = comb_conf
            else:
                self.full_correlations.append(
                    CorrelationRecord(
                        key_name=k_name,
                        slot_index=slot,
                        bank_index=slot // BANK_STRIDE,
                        column_index=slot % BANK_STRIDE,
                        byte_offset=off,
                        absolute_address=addr,
                        parameter_name=param,
                        confidence=comb_conf,
                        supporting_experiments=[out.spec.id],
                        notes=f"Key {k_corr.confidence.value if k_corr else '?'}, Offset {o_corr.confidence.value if o_corr else '?'}",
                    )
                )

    def format_terminal_tables(self) -> str:
        """Format clean tables for CLI presentation."""
        lines = []

        # Table 1: Experiments Execution Log
        lines.append("=== 1. Experiment Run Log ===")
        lines.append(f"Total experiments: {len(self.outcomes)}")
        lines.append("-" * 110)
        lines.append(f"{'ID':<18} {'Key':<6} {'Parameter':<18} {'Status':<14} {'Slot:Off':<10} {'Address':<10} {'Diff (Old->New)':<18} {'Notes'}")
        lines.append("-" * 110)

        for out in self.outcomes:
            diff_str = "-"
            slot_str = "-"
            addr_str = "-"
            if out.status == "ISOLATED_OK":
                slot_str = f"S{out.detected_slot:03d} : +{out.detected_offset}"
                addr_str = f"0x{out.detected_address:04X}"
                diff_str = f"0x{out.old_byte:02X} -> 0x{out.new_byte:02X} ({out.delta:+d})"
            elif out.status == "NOISY":
                diff_str = f"{len(out.diffs)} bytes touched"
                slot_str = f"S{out.detected_slot:03d} (noise)"

            notes = out.error_message or out.spec.description or "-"
            lines.append(
                f"{out.spec.id:<18} {out.spec.key_name:<6} {out.spec.parameter_name:<18} {out.status:<14} {slot_str:<10} {addr_str:<10} {diff_str:<18} {notes}"
            )
        lines.append("-" * 110)
        lines.append("")

        # Table 2: Unified Correlation Table
        lines.append("=== 2. Unified Correlation Table (Key -> Slot -> Byte Offset -> Parameter -> Confidence) ===")
        lines.append("-" * 105)
        lines.append(f"{'Key':<8} {'Slot':<8} {'Bank:Col':<12} {'Offset':<8} {'Address':<10} {'Parameter':<20} {'Confidence':<14} {'Supporting Experiments'}")
        lines.append("-" * 105)

        for rec in sorted(self.full_correlations, key=lambda r: (r.slot_index, r.byte_offset)):
            b_col = f"B{rec.bank_index}:C{rec.column_index:02d}"
            s_idx = f"S{rec.slot_index:03d}"
            off_s = f"+{rec.byte_offset}"
            addr_s = f"0x{rec.absolute_address:04X}"
            conf_s = f"[{rec.confidence.value}]"
            exps = ", ".join(rec.supporting_experiments)
            lines.append(
                f"{rec.key_name:<8} {s_idx:<8} {b_col:<12} {off_s:<8} {addr_s:<10} {rec.parameter_name:<20} {conf_s:<14} {exps}"
            )
        lines.append("-" * 105)
        lines.append("")

        # Table 3: Offset Structure Confidence
        lines.append("=== 3. 8-Byte Slot Offset Assignments ===")
        lines.append("-" * 90)
        lines.append(f"{'Offset':<8} {'Parameter':<22} {'Keys Tested':<20} {'Confidence':<14} {'Evidence / Notes'}")
        lines.append("-" * 90)

        for off in range(SLOT_SIZE):
            corr = self.offset_correlations.get(off)
            if corr:
                k_list = ", ".join(corr.keys_tested)
                conf_str = f"[{corr.confidence.value}]"
                lines.append(
                    f"+{off:<7} {corr.parameter_name:<22} {k_list:<20} {conf_str:<14} {corr.notes}"
                )
            else:
                conf_str = f"[{ConfidenceLevel.UNKNOWN.value}]"
                lines.append(
                    f"+{off:<7} {'[UNKNOWN]':<22} {'-':<20} {conf_str:<14} Not tested in experiments"
                )
        lines.append("-" * 90)

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert analysis report to JSON serializable dictionary."""
        return {
            "total_experiments": len(self.outcomes),
            "outcomes": [
                {
                    "id": o.spec.id,
                    "key_name": o.spec.key_name,
                    "parameter": o.spec.parameter_name,
                    "status": o.status,
                    "detected_slot": o.detected_slot,
                    "detected_offset": o.detected_offset,
                    "detected_address": f"0x{o.detected_address:04X}" if o.detected_address is not None else None,
                    "old_byte": f"0x{o.old_byte:02X}" if o.old_byte is not None else None,
                    "new_byte": f"0x{o.new_byte:02X}" if o.new_byte is not None else None,
                    "delta": o.delta,
                    "error": o.error_message,
                }
                for o in self.outcomes
            ],
            "correlations": [
                {
                    "key": r.key_name,
                    "slot_index": r.slot_index,
                    "bank_index": r.bank_index,
                    "column_index": r.column_index,
                    "byte_offset": r.byte_offset,
                    "absolute_address": f"0x{r.absolute_address:04X}",
                    "parameter": r.parameter_name,
                    "confidence": r.confidence.value,
                    "experiments": r.supporting_experiments,
                }
                for r in self.full_correlations
            ],
            "slot_offsets": {
                f"+{off}": {
                    "parameter": corr.parameter_name if corr else None,
                    "confidence": corr.confidence.value if corr else "UNKNOWN",
                    "keys_tested": corr.keys_tested if corr else [],
                }
                for off, corr in ((i, self.offset_correlations.get(i)) for i in range(SLOT_SIZE))
            },
        }

    def generate_markdown_report(self) -> str:
        """Generate structured markdown report."""
        lines = [
            "# Configuration Experiment Batch Analysis Report",
            "",
            "> **Confidence Rule:** A single experiment yields at most `PROBABLE` status. "
            "Status `CONFIRMED` is assigned only with $\\ge 2$ independent repeatable confirmations.",
            "",
            "## 1. Summary Correlation Table",
            "",
            "| Key | Slot | Bank:Column | Offset | Address | Parameter | Confidence | Experiments |",
            "| :--- | :---: | :---: | :---: | :---: | :--- | :---: | :--- |",
        ]

        for r in sorted(self.full_correlations, key=lambda x: (x.slot_index, x.byte_offset)):
            lines.append(
                f"| **{r.key_name}** | `S{r.slot_index:03d}` | B{r.bank_index}:C{r.column_index:02d} | "
                f"`+{r.byte_offset}` | `0x{r.absolute_address:04X}` | {r.parameter_name} | "
                f"`{r.confidence.value}` | {', '.join(r.supporting_experiments)} |"
            )

        lines.extend([
            "",
            "## 2. 8-Byte Slot Structure",
            "",
            "| Offset | Parameter | Tested Keys | Status |",
            "| :---: | :--- | :--- | :---: |",
        ])

        for off in range(SLOT_SIZE):
            corr = self.offset_correlations.get(off)
            if corr:
                keys_s = ", ".join(corr.keys_tested)
                lines.append(f"| `+{off}` | **{corr.parameter_name}** | {keys_s} | `{corr.confidence.value}` |")
            else:
                lines.append(f"| `+{off}` | *Unknown* | - | `UNKNOWN` |")

        return "\n".join(lines)
