"""
Safe, Deterministic Executor for ProfileWritePlan.

Executes a planned ProfileWritePlan against hardware or mock HID transport:
1. Validates plan and input constraints.
2. Executes write steps strictly in vendor-canonical order:
   - Remap Layer 1 (AA 22)
   - Remap Layer 2 (AA 26)
   - RGB Global (AA 23)
   - RGB Matrix (AA 24)
   - Macro Table (AA 25)
   - Hall RT Switch Matrix (AA 27)
   - DKS Table (AA 28)
3. For every chunk:
   - Transmits 64-byte AA output report via send_report.
   - Awaits 64-byte 55 input report via receive_report.
   - Validates ACK (prefix 0x55, opcode, size, address, payload echo).
   - FAILS FAST on any mismatch, transport timeout, or I/O error.
4. On completion of all writes:
   - Performs closed-loop readback of device state.
   - Compares readback state against the target Profile.
   - If readback does not match, marks execution as FAILED and returns detailed ProfileDiff.
5. Zero automatic rollback at this stage (records exact failure position and reports).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import struct
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Union

from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.plan import ProfileWritePlan, SubsystemWriteStep, WriteChunk
from keyboard_re.protocol.transport import HidTransport

if TYPE_CHECKING:
    from keyboard_re.models.state import DeviceState, Profile
    from keyboard_re.profile_manager import ProfileDiff


class ExecutionStatus(str, Enum):
    """Execution status of a ProfileWritePlan or step."""
    SUCCESS = "SUCCESS"
    NO_CHANGES = "NO_CHANGES"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


@dataclass
class ChunkExecutionResult:
    """Detailed execution result for a single 64-byte WriteChunk."""
    chunk_index: int
    address: int
    size: int
    packet: bytes
    expected_ack: bytes
    received_ack: Optional[bytes] = None
    success: bool = False
    error: Optional[str] = None


@dataclass
class StepExecutionResult:
    """Execution result for a single SubsystemWriteStep."""
    subsystem: str
    opcode: int
    description: str
    total_chunks: int
    executed_chunks: int = 0
    chunk_results: List[ChunkExecutionResult] = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None


@dataclass
class PlanExecutionResult:
    """
    Complete, structured result of executing a ProfileWritePlan.

    Contains full audit trail:
    - Overall status (SUCCESS, FAILED, ABORTED)
    - Profile identity
    - Step-by-step and chunk-by-chunk records
    - Exact failure location (step index, chunk index) if failed
    - Readback verification status and detailed ProfileDiff
    """
    status: ExecutionStatus
    profile_id: int
    profile_name: str
    total_steps: int
    executed_steps: int = 0
    total_packets: int = 0
    packets_sent: int = 0
    acks_received: int = 0
    step_results: List[StepExecutionResult] = field(default_factory=list)
    failed_step_index: Optional[int] = None
    failed_chunk_index: Optional[int] = None
    readback_verified: bool = False
    readback_diff: Optional[ProfileDiff] = None
    readback_state: Optional[DeviceState] = None
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def format_text(self) -> str:
        lines = [
            "=" * 70,
            f"PROFILE EXECUTION RESULT: {self.status.value}",
            "=" * 70,
            f"Profile: ID {self.profile_id} ('{self.profile_name}')",
            f"Steps Executed:   {self.executed_steps}/{self.total_steps}",
            f"Packets Sent:     {self.packets_sent}/{self.total_packets}",
            f"ACKs Received:    {self.acks_received}/{self.total_packets}",
            f"Readback Verified:{' YES' if self.readback_verified else ' NO'}",
        ]

        if self.error:
            lines.append(f"Error:            {self.error}")
            if self.failed_step_index is not None:
                lines.append(f"Failed at Step:   #{self.failed_step_index + 1}")
            if self.failed_chunk_index is not None:
                lines.append(f"Failed at Chunk:  #{self.failed_chunk_index}")

        lines.append("-" * 70)
        lines.append("Subsystem Step Details:")
        for idx, step_res in enumerate(self.step_results, 1):
            st = "OK" if step_res.success else "FAILED"
            lines.append(
                f"  {idx}. [{st}] [Opcode 0x{step_res.opcode:02X}] {step_res.subsystem.upper()}: "
                f"{step_res.executed_chunks}/{step_res.total_chunks} chunks"
            )
            if step_res.error:
                lines.append(f"     -> Error: {step_res.error}")

        if self.readback_diff is not None and self.readback_diff.has_changes:
            lines.append("-" * 70)
            lines.append("Post-write Readback Discrepancies:")
            for sub_name in self.readback_diff.changed_subsystems:
                sub_diff = self.readback_diff.get_subsystem(sub_name)
                if sub_diff:
                    lines.append(f"  * {sub_name}: {sub_diff.summary}")

        lines.append("=" * 70)
        return "\n".join(lines)


def validate_chunk_ack(
    chunk: WriteChunk,
    ack: bytes,
    strict_payload: bool = True,
) -> Optional[str]:
    """
    Validate a 64-byte device ACK input report against the expected WriteChunk ACK.

    Returns None if the ACK is valid, or a descriptive error message if invalid.
    """
    if len(ack) != REPORT_SIZE:
        return f"ACK size mismatch: expected {REPORT_SIZE} bytes, got {len(ack)}"

    # 1. Prefix: 0x55
    if ack[0] != 0x55:
        return f"Invalid ACK prefix 0x{ack[0]:02X}, expected 0x55"

    # 2. Opcode: must match expected opcode
    expected_opcode = chunk.expected_ack[1]
    if ack[1] != expected_opcode:
        return (
            f"Invalid ACK opcode 0x{ack[1]:02X}, expected 0x{expected_opcode:02X} "
            f"for chunk at address 0x{chunk.address:04X}"
        )

    # 3. Payload size: must match chunk size
    if ack[2] != chunk.size:
        return (
            f"ACK payload size mismatch: expected {chunk.size}, got {ack[2]} "
            f"for chunk at address 0x{chunk.address:04X}"
        )

    # 4. Address: must match chunk address
    ack_addr = struct.unpack_from("<H", ack, 3)[0]
    if ack_addr != chunk.address:
        return (
            f"ACK address mismatch: expected 0x{chunk.address:04X}, got 0x{ack_addr:04X}"
        )

    # 5. Payload echo: verify echoed data if strict_payload is enabled
    if strict_payload:
        expected_payload = chunk.expected_ack[8 : 8 + chunk.size]
        actual_payload = ack[8 : 8 + chunk.size]
        if actual_payload != expected_payload:
            return (
                f"ACK payload echo mismatch for chunk at address 0x{chunk.address:04X}: "
                f"expected {expected_payload[:8].hex().upper()}..., "
                f"got {actual_payload[:8].hex().upper()}..."
            )

    return None


class ProfilePlanExecutor:
    """
    Deterministic, fail-fast executor for ProfileWritePlan.

    Executes planned packets chunk-by-chunk with immediate ACK validation.
    Performs post-write closed-loop readback verification against target Profile.
    """

    SUBSYSTEM_KEY_MAP = {
        "remap_l1": "remap_l1",
        "remap_l2": "remap_l2",
        "rgb_global": "rgb_global",
        "rgb_matrix": "rgb_matrix",
        "macro": "macro_raw",
        "macro_raw": "macro_raw",
        "hall": "hall",
        "dks": "dks_raw",
        "dks_raw": "dks_raw",
        "game_mode": "game_mode",
    }

    def __init__(
        self,
        transport: HidTransport,
        timeout: float = 1.0,
        strict_payload: bool = True,
        readback_func: Optional[Callable[[HidTransport, float], DeviceState]] = None,
        logger: Optional[Callable[[str], None]] = None,
    ):
        self.transport = transport
        self.timeout = timeout
        self.strict_payload = strict_payload
        self.readback_func = readback_func
        self.logger = logger

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger(msg)

    def execute(
        self,
        plan: ProfileWritePlan,
        target_profile: Profile,
        verify_readback: bool = True,
    ) -> PlanExecutionResult:
        """
        Execute the given ProfileWritePlan.

        Safety:
        - Fails fast immediately on any unexpected ACK, timeout, or transport error.
        - Stops execution instantly and sends NO further packets if an error occurs.
        - Verifies device state via readback comparison against target_profile.
        """
        result = PlanExecutionResult(
            status=ExecutionStatus.SUCCESS,
            profile_id=plan.profile_id,
            profile_name=plan.profile_name,
            total_steps=len(plan.steps),
            total_packets=plan.total_packets,
        )

        # Early out for empty plan
        if plan.is_empty:
            self._log("ProfileWritePlan is empty; nothing to write.")
            result.readback_verified = True
            return result

        # Drain residual/stale reports in input buffer prior to write series
        if hasattr(self.transport, "drain_input_buffer"):
            try:
                drained = self.transport.drain_input_buffer()
                if drained:
                    self._log(f"Pre-write input buffer drained {drained} stale report(s).")
            except Exception as e:
                self._log(f"Warning during pre-write buffer drain: {e}")

        # ---------------------------------------------------------------------
        # 1. Execute Write Steps Sequentially
        # ---------------------------------------------------------------------
        for step_idx, step in enumerate(plan.steps):
            step_result = StepExecutionResult(
                subsystem=step.subsystem,
                opcode=step.opcode,
                description=step.description,
                total_chunks=step.packet_count,
            )
            result.step_results.append(step_result)

            self._log(
                f"Executing Step {step_idx + 1}/{len(plan.steps)}: "
                f"[{step.subsystem.upper()}] (Opcode 0x{step.opcode:02X}, {step.packet_count} chunks)"
            )

            for chunk_idx, chunk in enumerate(step.chunks):
                chunk_result = ChunkExecutionResult(
                    chunk_index=chunk.chunk_index,
                    address=chunk.address,
                    size=chunk.size,
                    packet=chunk.packet,
                    expected_ack=chunk.expected_ack,
                )
                step_result.chunk_results.append(chunk_result)

                # Send packet
                try:
                    validate_report_size(chunk.packet, REPORT_SIZE)
                    self.transport.send_report(0, chunk.packet)
                    result.packets_sent += 1
                except Exception as e:
                    err_msg = (
                        f"Transport send error on step #{step_idx + 1} ({step.subsystem}) "
                        f"chunk #{chunk.chunk_index} (addr 0x{chunk.address:04X}): {e}"
                    )
                    chunk_result.error = err_msg
                    step_result.error = err_msg
                    result.status = ExecutionStatus.FAILED
                    result.failed_step_index = step_idx
                    result.failed_chunk_index = chunk_idx
                    result.error = err_msg
                    self._log(f"FAIL-FAST ABORT: {err_msg}")
                    return result

                # Wait for ACK (transport handles stale ACK skipping within deadline)
                expected_opcode = chunk.expected_ack[1]
                expected_addr = chunk.address
                try:
                    try:
                        ack = self.transport.receive_report(
                            timeout=self.timeout,
                            expected_opcode=expected_opcode,
                            expected_address=expected_addr,
                        )
                    except TypeError:
                        ack = self.transport.receive_report(timeout=self.timeout)
                    chunk_result.received_ack = ack
                    result.acks_received += 1
                except Exception as e:
                    err_msg = (
                        f"Transport timeout/receive error waiting for ACK on step #{step_idx + 1} "
                        f"({step.subsystem}) chunk #{chunk.chunk_index} (addr 0x{chunk.address:04X}): {e}"
                    )
                    chunk_result.error = err_msg
                    step_result.error = err_msg
                    result.status = ExecutionStatus.FAILED
                    result.failed_step_index = step_idx
                    result.failed_chunk_index = chunk_idx
                    result.error = err_msg
                    self._log(f"FAIL-FAST ABORT: {err_msg}")
                    return result

                # Validate ACK
                val_err = validate_chunk_ack(chunk, ack, strict_payload=self.strict_payload)
                if val_err:
                    err_msg = (
                        f"ACK validation failed on step #{step_idx + 1} ({step.subsystem}) "
                        f"chunk #{chunk.chunk_index}: {val_err}"
                    )
                    chunk_result.error = err_msg
                    step_result.error = err_msg
                    result.status = ExecutionStatus.FAILED
                    result.failed_step_index = step_idx
                    result.failed_chunk_index = chunk_idx
                    result.error = err_msg
                    self._log(f"FAIL-FAST ABORT: {err_msg}")
                    return result

                # Chunk success
                chunk_result.success = True
                step_result.executed_chunks += 1

            step_result.success = True
            result.executed_steps += 1

        # ---------------------------------------------------------------------
        # 2. Closed-Loop Readback Verification
        # ---------------------------------------------------------------------
        if not verify_readback:
            self._log("Readback verification disabled by caller.")
            result.readback_verified = False
            return result

        self._log("Performing post-write readback verification...")
        try:
            if self.readback_func:
                readback_state = self.readback_func(self.transport, self.timeout)
            else:
                from keyboard_re.models.state import collect_device_state
                readback_state = collect_device_state(self.transport, timeout=self.timeout)
            result.readback_state = readback_state
        except Exception as e:
            err_msg = f"Post-write device readback failed: {e}"
            result.status = ExecutionStatus.FAILED
            result.error = err_msg
            result.readback_verified = False
            self._log(f"READBACK ERROR: {err_msg}")
            return result

        # Diff readback state against target profile
        from keyboard_re.profile_manager import ProfileManager
        mgr = ProfileManager()
        readback_diff = mgr.compare_profile_with_state(
            target_profile,
            readback_state,
            is_readback=True,
        )
        result.readback_diff = readback_diff

        # Check for discrepancies on all subsystems modified by the plan
        mismatches: List[str] = []
        for step in plan.steps:
            diff_sub_name = self.SUBSYSTEM_KEY_MAP.get(step.subsystem, step.subsystem)
            sub_diff = readback_diff.get_subsystem(diff_sub_name)
            if sub_diff and sub_diff.has_changes:
                mismatches.append(f"{step.subsystem} ({sub_diff.summary})")

        # Also check any subsystem that target_profile explicitly defines
        for sub_name, sub_diff in readback_diff.subsystems.items():
            if not sub_diff.has_changes:
                continue
            # If target profile explicitly defined this subsystem and it differs:
            target_val = getattr(target_profile, sub_name, None)
            if sub_name == "remap_l1":
                target_val = target_profile.remap
            elif sub_name == "macro_raw":
                target_val = target_profile.macros_raw
            elif sub_name == "dks_raw":
                target_val = target_profile.dks_raw

            if target_val is not None:
                desc = f"{sub_name} ({sub_diff.summary})"
                if desc not in mismatches:
                    mismatches.append(desc)

        if mismatches:
            err_msg = (
                f"Readback verification mismatch on {len(mismatches)} subsystem(s): "
                f"{'; '.join(mismatches)}"
            )
            result.status = ExecutionStatus.FAILED
            result.readback_verified = False
            result.error = err_msg
            self._log(f"READBACK MISMATCH: {err_msg}")
            return result

        result.readback_verified = True
        result.status = ExecutionStatus.SUCCESS
        self._log("Profile applied and verified successfully via readback.")
        return result


def execute_profile_write_plan(
    transport: HidTransport,
    plan: ProfileWritePlan,
    target_profile: Profile,
    timeout: float = 1.0,
    strict_payload: bool = True,
    readback_func: Optional[Callable[[HidTransport, float], DeviceState]] = None,
    verify_readback: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> PlanExecutionResult:
    """
    Convenience function to execute a ProfileWritePlan with closed-loop verification.

    Args:
        transport: Connected HidTransport (e.g. NativeHidTransport or MockHidTransport).
        plan: Prepared ProfileWritePlan.
        target_profile: The target Profile against which post-write state is verified.
        timeout: Read/write timeout in seconds.
        strict_payload: Whether to verify that ACK echoes the chunk payload.
        readback_func: Optional custom readback callable (transport, timeout) -> DeviceState.
        verify_readback: Whether to perform post-write readback verification.
        logger: Optional logging callback.

    Returns:
        PlanExecutionResult containing complete audit record and status.
    """
    executor = ProfilePlanExecutor(
        transport=transport,
        timeout=timeout,
        strict_payload=strict_payload,
        readback_func=readback_func,
        logger=logger,
    )
    return executor.execute(plan, target_profile, verify_readback=verify_readback)
