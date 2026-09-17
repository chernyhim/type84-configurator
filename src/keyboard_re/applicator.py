"""
High-Level Profile Application Facade for IO by Red Square Type 84 Magnetic Black.

Unifies and orchestrates the existing core subsystems:
1. collect_device_state() -> Read current hardware state
2. ProfileManager.compare_profile_with_state() -> Compute structured ProfileDiff
3. build_profile_write_plan() -> Build canonical dry-run ProfileWritePlan
4. [Safety Gate & Confirmation] -> Present PlanConfirmationSummary and enforce approval
5. ProfilePlanExecutor -> Transmit chunks with fail-fast ACK validation
6. Closed-loop Readback Verification -> Ensure target profile matches device state

Safety Guarantees:
- Zero writes if target profile is identical (status=NO_CHANGES).
- Zero writes in preview / dry_run mode (status=SUCCESS, dry_run=True).
- Zero writes if confirmation callback is omitted or returns False (status=ABORTED).
- Reuses ProfilePlanExecutor directly; does NOT duplicate ACK/readback logic.
- Preserves full audit trail in ApplyProfileResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from keyboard_re.profile_manager import ProfileDiff, ProfileManager
from keyboard_re.protocol.executor import (
    ExecutionStatus,
    PlanExecutionResult,
    execute_profile_write_plan,
)
from keyboard_re.protocol.plan import ProfileWritePlan, build_profile_write_plan
from keyboard_re.protocol.transport import HidTransport

if TYPE_CHECKING:
    from keyboard_re.models.state import DeviceState, Profile


@dataclass(frozen=True)
class PlanConfirmationSummary:
    """
    Structured summary presented to the user / caller before physical transmission.

    Provides complete visibility into planned hardware modifications:
    - Profile identity
    - Changed subsystems and diff count
    - Total HID report packets to be sent
    - Step-by-step breakdown (subsystem, opcode, packet count, description)
    - Prominent safety warning
    """
    profile_id: int
    profile_name: str
    changed_subsystems: List[str]
    total_packets: int
    steps_summary: List[Dict[str, Any]]
    total_diff_changes: int
    subsystem_diffs_summary: Dict[str, str]
    warning: str = (
        "WARNING: This operation will physically overwrite keyboard persistent flash memory!"
    )

    def format_text(self) -> str:
        """Produce formatted, human-readable confirmation summary."""
        lines = [
            "=" * 70,
            "PROFILE APPLICATION CONFIRMATION SUMMARY",
            "=" * 70,
            f"Target Profile:       ID {self.profile_id} ('{self.profile_name}')",
            f"Changed Subsystems:   {', '.join(self.changed_subsystems) if self.changed_subsystems else 'None'}",
            f"Total Subsystem Diffs:{self.total_diff_changes} change(s)",
            f"Total HID Reports:    {self.total_packets} packet(s)",
            "-" * 70,
            "Planned Subsystem Steps:",
        ]
        for idx, step in enumerate(self.steps_summary, 1):
            lines.append(
                f"  {idx}. [Opcode 0x{step['opcode']:02X}] {step['subsystem'].upper()}: "
                f"{step['packet_count']} packet(s) - {step['description']}"
            )
        lines.append("-" * 70)
        lines.append(self.warning)
        lines.append("=" * 70)
        return "\n".join(lines)


@dataclass
class ApplyProfileResult:
    """
    Comprehensive structured result of applying a Profile to a keyboard device.

    Status values:
    - NO_CHANGES: Target profile is already identical to device state (0 writes).
    - SUCCESS: Profile applied and verified via readback, or dry_run completed.
    - ABORTED: Operation aborted due to rejected/missing confirmation (0 writes).
    - FAILED: Device read, packet transmission, ACK validation, or readback failed.
    """
    status: ExecutionStatus
    profile_id: int
    profile_name: str
    diff: Optional[ProfileDiff] = None
    plan: Optional[ProfileWritePlan] = None
    confirmation_summary: Optional[PlanConfirmationSummary] = None
    confirmed: Optional[bool] = None
    execution_result: Optional[PlanExecutionResult] = None
    dry_run: bool = False
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """True if operation succeeded or was a successful no-op."""
        return self.status in (ExecutionStatus.SUCCESS, ExecutionStatus.NO_CHANGES)

    @property
    def is_no_changes(self) -> bool:
        """True if target profile was already identical to device state."""
        return self.status == ExecutionStatus.NO_CHANGES

    def format_text(self) -> str:
        """Produce formatted audit report."""
        lines = [
            "=" * 70,
            f"APPLY PROFILE RESULT: {self.status.value}",
            "=" * 70,
            f"Profile:       ID {self.profile_id} ('{self.profile_name}')",
            f"Dry Run Mode:  {'YES' if self.dry_run else 'NO'}",
            f"Confirmed:     {self.confirmed if self.confirmed is not None else 'N/A'}",
        ]
        if self.plan is not None:
            lines.append(f"Planned Steps: {len(self.plan.steps)} step(s), {self.plan.total_packets} packet(s)")
        if self.execution_result is not None:
            lines.append(
                f"Execution:     {self.execution_result.packets_sent}/{self.execution_result.total_packets} sent, "
                f"Readback Verified: {'YES' if self.execution_result.readback_verified else 'NO'}"
            )
        if self.error:
            lines.append(f"Error:         {self.error}")
        lines.append("=" * 70)
        return "\n".join(lines)


def build_confirmation_summary(plan: ProfileWritePlan, diff: ProfileDiff) -> PlanConfirmationSummary:
    """Build PlanConfirmationSummary from ProfileWritePlan and ProfileDiff."""
    steps_info = [
        {
            "subsystem": step.subsystem,
            "opcode": step.opcode,
            "packet_count": step.packet_count,
            "description": step.description,
        }
        for step in plan.steps
    ]
    sub_diffs = {
        name: sub.summary
        for name, sub in diff.subsystems.items()
        if sub.has_changes
    }
    return PlanConfirmationSummary(
        profile_id=plan.profile_id,
        profile_name=plan.profile_name,
        changed_subsystems=plan.modified_subsystems,
        total_packets=plan.total_packets,
        steps_summary=steps_info,
        total_diff_changes=diff.total_changes,
        subsystem_diffs_summary=sub_diffs,
    )


def apply_profile(
    transport: HidTransport,
    profile: Profile,
    *,
    confirm_callback: Optional[Callable[[PlanConfirmationSummary], bool]] = None,
    dry_run: bool = False,
    timeout: float = 1.0,
    strict_payload: bool = True,
    verify_readback: bool = True,
    current_state: Optional[DeviceState] = None,
    readback_func: Optional[Callable[[HidTransport, float], DeviceState]] = None,
    profile_manager: Optional[ProfileManager] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> ApplyProfileResult:
    """
    Safe, high-level facade for applying a Profile to a connected keyboard device.

    Workflow:
    1. Collect current device state (or use provided current_state).
    2. Compare current state with target profile to build ProfileDiff.
    3. Generate canonical dry-run ProfileWritePlan.
    4. If plan.is_empty -> Return status=NO_CHANGES immediately (0 writes).
    5. If dry_run=True -> Return status=SUCCESS with plan & summary (0 writes).
    6. Build PlanConfirmationSummary and invoke confirm_callback.
       If confirm_callback is None or returns False -> Return status=ABORTED (0 writes).
    7. Execute plan via ProfilePlanExecutor (fails fast on ACK mismatch/timeout).
    8. Perform closed-loop readback verification against target profile.
    9. Return structured ApplyProfileResult.

    Args:
        transport: Connected HidTransport (MockHidTransport or NativeHidTransport).
        profile: The target Profile to apply.
        confirm_callback: Optional user confirmation callback. Receives PlanConfirmationSummary,
                          must return True to allow hardware writes.
        dry_run: If True, previews changes without transmitting any packets.
        timeout: Read/write timeout in seconds.
        strict_payload: Whether to strictly verify echoed payload in ACK packets.
        verify_readback: Whether to execute post-write readback verification.
        current_state: Optional pre-read DeviceState (if None, reads via collect_device_state).
        readback_func: Optional custom readback callable (transport, timeout) -> DeviceState.
        profile_manager: Optional ProfileManager instance.
        logger: Optional logging callback.

    Returns:
        ApplyProfileResult with full execution details and status.
    """
    pid = profile.profile_id
    pname = profile.name or f"Profile {pid}"
    mgr = profile_manager or ProfileManager()

    def _log(msg: str) -> None:
        if logger:
            logger(msg)

    # -------------------------------------------------------------------------
    # 1. Collect Current Hardware State
    # -------------------------------------------------------------------------
    if current_state is None:
        _log(f"Reading current keyboard device state...")
        try:
            from keyboard_re.models.state import collect_device_state
            current_state = collect_device_state(transport, timeout=timeout)
        except Exception as e:
            err_msg = f"Failed to collect current device state: {e}"
            _log(f"ERROR: {err_msg}")
            return ApplyProfileResult(
                status=ExecutionStatus.FAILED,
                profile_id=pid,
                profile_name=pname,
                dry_run=dry_run,
                error=err_msg,
            )

    # -------------------------------------------------------------------------
    # 2. Compute ProfileDiff & Build ProfileWritePlan
    # -------------------------------------------------------------------------
    _log(f"Comparing target profile #{pid} against current device state...")
    diff = mgr.compare_profile_with_state(profile, current_state)
    plan = build_profile_write_plan(current_state, profile)

    # -------------------------------------------------------------------------
    # 3. Handle No-Op (Identical State)
    # -------------------------------------------------------------------------
    if plan.is_empty:
        _log(f"Target profile #{pid} is identical to current device state. No changes required.")
        return ApplyProfileResult(
            status=ExecutionStatus.NO_CHANGES,
            profile_id=pid,
            profile_name=pname,
            diff=diff,
            plan=plan,
            dry_run=dry_run,
        )

    # Build confirmation summary for display or callback
    summary = build_confirmation_summary(plan, diff)

    # -------------------------------------------------------------------------
    # 4. Handle Dry-Run Preview Mode
    # -------------------------------------------------------------------------
    if dry_run:
        _log(f"Dry-run preview: {len(plan.steps)} step(s), {plan.total_packets} packet(s). Zero writes executed.")
        return ApplyProfileResult(
            status=ExecutionStatus.SUCCESS,
            profile_id=pid,
            profile_name=pname,
            diff=diff,
            plan=plan,
            confirmation_summary=summary,
            dry_run=True,
        )

    # -------------------------------------------------------------------------
    # 5. Safety Gate: Enforce Confirmation
    # -------------------------------------------------------------------------
    if confirm_callback is None:
        err_msg = (
            f"Write operation requires explicit confirmation, but confirm_callback was not provided. "
            f"Aborting physical write for profile #{pid} ({plan.total_packets} packets)."
        )
        _log(f"SAFETY ABORT: {err_msg}")
        return ApplyProfileResult(
            status=ExecutionStatus.ABORTED,
            profile_id=pid,
            profile_name=pname,
            diff=diff,
            plan=plan,
            confirmation_summary=summary,
            confirmed=False,
            error=err_msg,
        )

    try:
        is_confirmed = bool(confirm_callback(summary))
    except Exception as e:
        err_msg = f"Confirmation callback raised an exception: {e}"
        _log(f"SAFETY ABORT: {err_msg}")
        return ApplyProfileResult(
            status=ExecutionStatus.ABORTED,
            profile_id=pid,
            profile_name=pname,
            diff=diff,
            plan=plan,
            confirmation_summary=summary,
            confirmed=False,
            error=err_msg,
        )

    if not is_confirmed:
        err_msg = f"Profile application was rejected by user/confirmation callback for profile #{pid}."
        _log(f"SAFETY ABORT: {err_msg}")
        return ApplyProfileResult(
            status=ExecutionStatus.ABORTED,
            profile_id=pid,
            profile_name=pname,
            diff=diff,
            plan=plan,
            confirmation_summary=summary,
            confirmed=False,
            error=err_msg,
        )

    # -------------------------------------------------------------------------
    # 6. Execute Plan via ProfilePlanExecutor
    # -------------------------------------------------------------------------
    _log(f"Confirmation granted. Executing write plan ({plan.total_packets} packets)...")
    exec_res = execute_profile_write_plan(
        transport=transport,
        plan=plan,
        target_profile=profile,
        timeout=timeout,
        strict_payload=strict_payload,
        readback_func=readback_func,
        verify_readback=verify_readback,
        logger=logger,
    )

    return ApplyProfileResult(
        status=exec_res.status,
        profile_id=pid,
        profile_name=pname,
        diff=diff,
        plan=plan,
        confirmation_summary=summary,
        confirmed=True,
        execution_result=exec_res,
        dry_run=False,
        error=exec_res.error,
    )


class ProfileApplicator:
    """
    High-level application service configuring and executing profile deployments.
    """

    def __init__(
        self,
        transport: HidTransport,
        profile_manager: Optional[ProfileManager] = None,
        timeout: float = 1.0,
        strict_payload: bool = True,
        verify_readback: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ):
        self.transport = transport
        self.profile_manager = profile_manager or ProfileManager()
        self.timeout = timeout
        self.strict_payload = strict_payload
        self.verify_readback = verify_readback
        self.logger = logger

    def apply(
        self,
        profile: Profile,
        *,
        confirm_callback: Optional[Callable[[PlanConfirmationSummary], bool]] = None,
        dry_run: bool = False,
        current_state: Optional[DeviceState] = None,
        readback_func: Optional[Callable[[HidTransport, float], DeviceState]] = None,
    ) -> ApplyProfileResult:
        """Apply a profile to the keyboard device."""
        return apply_profile(
            transport=self.transport,
            profile=profile,
            confirm_callback=confirm_callback,
            dry_run=dry_run,
            timeout=self.timeout,
            strict_payload=self.strict_payload,
            verify_readback=self.verify_readback,
            current_state=current_state,
            readback_func=readback_func,
            profile_manager=self.profile_manager,
            logger=self.logger,
        )

    def preview(
        self,
        profile: Profile,
        *,
        current_state: Optional[DeviceState] = None,
    ) -> ApplyProfileResult:
        """Preview profile application in dry-run mode (guaranteed zero writes)."""
        return self.apply(profile, dry_run=True, current_state=current_state)
