"""
Hardware Output Layer for Keyboard Visualizer.

Connects the visualizer frame pipeline to the physical keyboard's Per-Key RGB
subsystem using the existing, verified AA24 protocol chunk builder and validator.

Responsibilities:
- Reads and preserves the initial 512-byte Per-Key RGB state (AA14) upon starting.
- Reads and preserves the initial Global RGB state (AA13) upon starting.
- Switches Global RGB to EFFECT_CUSTOM (0x80) via AA23 so the hardware renders Per-Key frames.
- Transmits full 512-byte RGBFrames (10 chunks: 9x56B + 1x8B tail at 504) via AA24.
- Restores both saved baseline states (Per-Key AA14 + Global RGB AA13) upon stop, error, or close.
- Ensures thread-safe serialized transmission over the HID transport.
- Clean error encapsulation via HardwareOutputError.
- Completely isolated from decoding, playback timing, and profile management.
"""

from __future__ import annotations

from dataclasses import replace
import logging
import threading
import time
from typing import Optional, TYPE_CHECKING

from keyboard_re.protocol.executor import validate_chunk_ack
from keyboard_re.protocol.packets import REPORT_SIZE, validate_report_size
from keyboard_re.protocol.plan import (
    build_rgb_global_write_chunk,
    build_rgb_matrix_write_chunks,
)
from keyboard_re.protocol.read import read_rgb_global, read_rgb_per_key
from keyboard_re.protocol.rgb import EFFECT_CUSTOM, RGBGlobalConfig
from keyboard_re.protocol.transport import HidTransport
from keyboard_re.visualizer.frame import RGBFrame
from keyboard_re.visualizer.telemetry import (
    ChunkTiming,
    FrameTelemetryRecord,
    VisualizerTelemetry,
)

if TYPE_CHECKING:
    from keyboard_re.visualizer.decoder import DecodedFrame
    from keyboard_re.visualizer.player import PlaybackEngine
    from keyboard_re.visualizer.processor import FrameProcessor, FrameProcessorConfig

logger = logging.getLogger(__name__)


class HardwareOutputError(Exception):
    """Raised when hardware transmission or ACK verification fails."""
    pass


class KeyboardRgbOutput:
    """
    Dedicated hardware output controller for streaming RGB frames to the keyboard.

    Implements:
    - Zero FPS management or sleep() calls (PlaybackEngine owns timing).
    - Full 512-byte LED buffer transmission (byte-exact, non-differential in Phase 4).
    - Backup of initial Per-Key RGB state via AA14 before streaming starts.
    - Backup of initial Global RGB state via AA13 before streaming starts.
    - Automatic activation of EFFECT_CUSTOM (0x80) via AA23 before frame output.
    - Automatic restoration of both states on stop or error.
    - Thread-safe serialization across worker and GUI threads.
    """

    def __init__(
        self,
        transport: Optional[HidTransport] = None,
        timeout: float = 1.0,
        strict_ack: bool = True,
        telemetry: Optional[VisualizerTelemetry] = None,
    ) -> None:
        self._transport = transport
        self._timeout = float(timeout)
        self._strict_ack = bool(strict_ack)
        self._telemetry = telemetry

        self._lock = threading.Lock()
        self._is_active: bool = False
        self._saved_rgb_buffer: Optional[bytes] = None
        self._saved_rgb_global: Optional[RGBGlobalConfig] = None
        self._frames_written: int = 0

        # Attached PlaybackEngine references
        self._attached_engine: Optional[PlaybackEngine] = None
        self._attached_processor: Optional[FrameProcessor] = None
        self._attached_config: Optional[FrameProcessorConfig] = None

    @property
    def transport(self) -> Optional[HidTransport]:
        """Return the current HID transport."""
        return self._transport

    @transport.setter
    def transport(self, transport: Optional[HidTransport]) -> None:
        with self._lock:
            self._transport = transport

    @property
    def is_active(self) -> bool:
        """Return True if output is actively running and accepting frames."""
        with self._lock:
            return self._is_active

    @property
    def saved_rgb_buffer(self) -> Optional[bytes]:
        """Return the saved initial Per-Key RGB state (512 bytes), if captured."""
        with self._lock:
            return self._saved_rgb_buffer

    @property
    def saved_rgb_global(self) -> Optional[RGBGlobalConfig]:
        """Return the saved initial Global RGB configuration, if captured."""
        with self._lock:
            return self._saved_rgb_global

    @property
    def frames_written(self) -> int:
        """Return total count of complete frames successfully written."""
        with self._lock:
            return self._frames_written

    @property
    def attached_engine(self) -> Optional[PlaybackEngine]:
        """Return the currently attached PlaybackEngine, if any."""
        return self._attached_engine

    @property
    def telemetry(self) -> Optional[VisualizerTelemetry]:
        """Return attached VisualizerTelemetry instance, if any."""
        return self._telemetry

    @telemetry.setter
    def telemetry(self, telem: Optional[VisualizerTelemetry]) -> None:
        self._telemetry = telem

    # -------------------------------------------------------------------------
    # Lifecycle: Start, Stop, Restore
    # -------------------------------------------------------------------------
    def start_visualizer(
        self,
        engine: Optional[PlaybackEngine] = None,
        processor: Optional[FrameProcessor] = None,
        processor_config: Optional[FrameProcessorConfig] = None,
    ) -> None:
        """
        Start visualizer hardware output.

        1. Validates transport connectivity.
        2. Reads and preserves current 512-byte Per-Key RGB state via AA14.
        3. Reads and preserves current Global RGB configuration via AA13.
        4. Switches Global RGB to EFFECT_CUSTOM (0x80) via AA23 so hardware renders Per-Key LED buffer.
        5. Marks output as active for writing frames.
        6. Optionally attaches to a PlaybackEngine.
        """
        if self._transport is None:
            raise HardwareOutputError("Cannot start visualizer: No HID transport configured")

        if hasattr(self._transport, "is_connected") and not self._transport.is_connected:
            raise HardwareOutputError("Cannot start visualizer: Keyboard device is disconnected or closed")

        with self._lock:
            if self._is_active:
                logger.warning("Visualizer hardware output is already active.")
                return

            # 1. Read and preserve current hardware Per-Key RGB state (AA14)
            try:
                self._saved_rgb_buffer = read_rgb_per_key(self._transport, timeout=self._timeout)
                logger.info("Preserved initial Per-Key RGB state (%d bytes)", len(self._saved_rgb_buffer))
            except Exception as exc:
                raise HardwareOutputError(
                    f"Failed to read current Per-Key RGB state via AA14: {exc}"
                ) from exc

            # 2. Read and preserve current Global RGB state (AA13)
            try:
                self._saved_rgb_global = read_rgb_global(self._transport, timeout=self._timeout)
                logger.info(
                    "Preserved initial Global RGB state (effect=0x%02X)",
                    self._saved_rgb_global.effect,
                )
            except Exception as exc:
                raise HardwareOutputError(
                    f"Failed to read current Global RGB state via AA13: {exc}"
                ) from exc

            # 3. Switch Global RGB to EFFECT_CUSTOM (0x80) via AA23
            try:
                custom_cfg = replace(
                    self._saved_rgb_global,
                    effect=EFFECT_CUSTOM,
                    speed=max(1, min(5, self._saved_rgb_global.speed or 3)),
                    brightness=max(1, min(5, self._saved_rgb_global.brightness or 5)),
                )
                self._set_global_config_locked(custom_cfg)
                logger.info("Switched Global RGB to Custom Per-Key mode (0x80)")
            except Exception as exc:
                self._emergency_restore_locked()
                raise HardwareOutputError(
                    f"Failed to switch Global RGB to Custom mode (0x80): {exc}"
                ) from exc

            self._is_active = True
            self._frames_written = 0

        if engine is not None:
            self.attach_to_engine(engine, processor, processor_config)

    # Convenience alias
    start = start_visualizer

    def stop_visualizer(self, restore: bool = True) -> None:
        """
        Stop visualizer hardware output.

        1. Halts attached PlaybackEngine if present.
        2. Stops accepting new frame writes.
        3. Restores saved initial Per-Key RGB state and Global RGB state.
        """
        # 1. Stop attached engine first so it stops emitting new frames
        if self._attached_engine is not None:
            try:
                self._attached_engine.stop()
            except Exception as exc:
                logger.warning("Error stopping attached PlaybackEngine: %s", exc)

        # 2. Deactivate output under lock
        with self._lock:
            self._is_active = False

        # 3. Restore previous RGB states
        if restore:
            self.restore_previous_rgb()

    # Convenience alias
    stop = stop_visualizer

    def restore_previous_rgb(self) -> None:
        """
        Restore both initial 512-byte Per-Key RGB state (AA14) and
        Global RGB state (AA23) saved during start_visualizer().
        """
        with self._lock:
            self._emergency_restore_locked()

    def _emergency_restore_locked(self) -> None:
        """Helper to restore both states while self._lock is already held."""
        errors = []

        # Restore Per-Key RGB buffer via AA24
        if self._saved_rgb_buffer is not None:
            try:
                self._write_raw_buffer_locked(self._saved_rgb_buffer)
                logger.info("Restored baseline Per-Key RGB state (%d bytes)", len(self._saved_rgb_buffer))
            except Exception as exc:
                logger.error("Failed to restore Per-Key RGB state: %s", exc)
                errors.append(f"Per-Key restore failed: {exc}")

        # Restore Global RGB state via AA23
        if self._saved_rgb_global is not None:
            try:
                restore_cfg = replace(
                    self._saved_rgb_global,
                    speed=max(1, min(5, self._saved_rgb_global.speed or 3)),
                    brightness=max(0, min(5, self._saved_rgb_global.brightness)),
                )
                self._set_global_config_locked(restore_cfg)
                logger.info(
                    "Restored baseline Global RGB state (effect=0x%02X)",
                    self._saved_rgb_global.effect,
                )
            except Exception as exc:
                logger.error("Failed to restore Global RGB state: %s", exc)
                errors.append(f"Global RGB restore failed: {exc}")

        if errors:
            raise HardwareOutputError("; ".join(errors))

    # -------------------------------------------------------------------------
    # Frame Transmission
    # -------------------------------------------------------------------------
    def write_frame(
        self,
        frame: RGBFrame,
        t_processor_ms: float = 0.0,
        t_callback_interval_ms: float = 0.0,
        t_decoder_ms: float = 0.0,
    ) -> None:
        """
        Transmit an RGBFrame to the physical keyboard.

        Serializes the frame into a full 512-byte buffer and transmits all 10 AA24
        chunks using the verified protocol writer.

        Note: Hardware writer does NOT sleep or regulate FPS. PlaybackEngine is
        the sole owner of timing.
        """
        if not isinstance(frame, RGBFrame):
            raise TypeError(f"Expected RGBFrame instance, got {type(frame).__name__}")

        with self._lock:
            if not self._is_active:
                logger.debug("KeyboardRgbOutput is not active; dropping frame.")
                return

            telem = self._telemetry
            is_telem = telem is not None and telem.enabled

            t_ser_start = time.perf_counter() if is_telem else 0.0
            buffer = frame.to_led_buffer()
            t_ser_end = time.perf_counter() if is_telem else 0.0
            t_serialize_ms = (t_ser_end - t_ser_start) * 1000.0 if is_telem else 0.0

            chunk_timings: Optional[list[ChunkTiming]] = [] if is_telem else None
            t_write_start = time.perf_counter() if is_telem else 0.0

            try:
                self._write_raw_buffer_locked(buffer, chunk_timings=chunk_timings)
                self._frames_written += 1

                if is_telem and chunk_timings is not None:
                    t_write_end = time.perf_counter()
                    t_write_ms = (t_write_end - t_write_start) * 1000.0
                    t_10_chunks = sum(c.total_ms for c in chunk_timings)
                    record = FrameTelemetryRecord(
                        frame_index=self._frames_written,
                        t_decoder_ms=t_decoder_ms,
                        t_callback_interval_ms=t_callback_interval_ms,
                        t_processor_ms=t_processor_ms,
                        t_serialize_ms=t_serialize_ms,
                        t_write_frame_ms=t_write_ms,
                        t_10_chunks_ms=t_10_chunks,
                        chunk_timings=chunk_timings,
                    )
                    telem.record_frame(record)

            except Exception as exc:
                # On failure: deactivate and attempt to restore previous states
                self._is_active = False
                logger.error("Error writing RGB frame to hardware: %s. Attempting restore...", exc)
                try:
                    self._emergency_restore_locked()
                except Exception as restore_exc:
                    logger.warning("Failed to restore RGB states after write failure: %s", restore_exc)
                raise HardwareOutputError(f"Failed to write RGB frame to hardware: {exc}") from exc

    def _write_raw_buffer_locked(
        self,
        buffer: bytes,
        chunk_timings: Optional[list[ChunkTiming]] = None,
    ) -> None:
        """
        Low-level transmit loop for a 512-byte LED buffer.
        Caller MUST hold self._lock.
        """
        if self._transport is None:
            raise HardwareOutputError("Cannot write RGB buffer: No transport configured")

        if hasattr(self._transport, "is_connected") and not self._transport.is_connected:
            raise HardwareOutputError("Cannot write RGB buffer: Keyboard device is disconnected or closed")

        if len(buffer) != 512:
            raise ValueError(f"RGB matrix buffer must be exactly 512 bytes, got {len(buffer)}")

        # Drain residual input reports before write series if supported
        if hasattr(self._transport, "drain_input_buffer"):
            try:
                self._transport.drain_input_buffer()
            except Exception as exc:
                logger.debug("Warning during pre-write buffer drain: %s", exc)

        # Build 10 sequential write chunks via existing verified API (AA 24)
        chunks = build_rgb_matrix_write_chunks(buffer)

        for chunk in chunks:
            validate_report_size(chunk.packet, REPORT_SIZE)

            t0 = time.perf_counter() if chunk_timings is not None else 0.0

            # Send output report
            try:
                self._transport.send_report(0, chunk.packet)
            except Exception as exc:
                raise HardwareOutputError(
                    f"Transport send error for AA24 chunk #{chunk.chunk_index} "
                    f"(address 0x{chunk.address:04X}): {exc}"
                ) from exc

            t1 = time.perf_counter() if chunk_timings is not None else 0.0

            # Receive ACK with opcode & address filtering
            try:
                try:
                    ack = self._transport.receive_report(
                        timeout=self._timeout,
                        expected_opcode=0x24,
                        expected_address=chunk.address,
                    )
                except TypeError:
                    ack = self._transport.receive_report(timeout=self._timeout)
            except Exception as exc:
                raise HardwareOutputError(
                    f"Transport timeout/receive error waiting for ACK on AA24 chunk #{chunk.chunk_index} "
                    f"(address 0x{chunk.address:04X}): {exc}"
                ) from exc

            t2 = time.perf_counter() if chunk_timings is not None else 0.0

            # Validate ACK using existing protocol validator
            val_err = validate_chunk_ack(chunk, ack, strict_payload=self._strict_ack)
            if val_err:
                raise HardwareOutputError(
                    f"ACK validation failed on AA24 chunk #{chunk.chunk_index} "
                    f"(address 0x{chunk.address:04X}): {val_err}"
                )

            if chunk_timings is not None:
                t3 = time.perf_counter()
                chunk_timings.append(
                    ChunkTiming(
                        chunk_index=chunk.chunk_index,
                        address=chunk.address,
                        send_ms=(t1 - t0) * 1000.0,
                        ack_ms=(t2 - t1) * 1000.0,
                        total_ms=(t3 - t0) * 1000.0,
                    )
                )

    def _set_global_config_locked(self, config: RGBGlobalConfig) -> None:
        """
        Send AA23 write packet to set Global RGB settings and validate device ACK.
        Caller MUST hold self._lock.
        """
        if self._transport is None:
            raise HardwareOutputError("Cannot write Global RGB: No transport configured")

        chunk = build_rgb_global_write_chunk(config)
        validate_report_size(chunk.packet, REPORT_SIZE)

        try:
            self._transport.send_report(0, chunk.packet)
        except Exception as exc:
            raise HardwareOutputError(f"Transport send error for AA23 Global RGB: {exc}") from exc

        try:
            try:
                ack = self._transport.receive_report(
                    timeout=self._timeout,
                    expected_opcode=0x23,
                )
            except TypeError:
                ack = self._transport.receive_report(timeout=self._timeout)
        except Exception as exc:
            raise HardwareOutputError(f"Transport timeout/receive error for AA23 Global RGB: {exc}") from exc

        val_err = validate_chunk_ack(chunk, ack, strict_payload=self._strict_ack)
        if val_err:
            raise HardwareOutputError(f"AA23 Global RGB ACK validation failed: {val_err}")

    # -------------------------------------------------------------------------
    # PlaybackEngine Pipeline Integration
    # -------------------------------------------------------------------------
    def attach_to_engine(
        self,
        engine: PlaybackEngine,
        processor: Optional[FrameProcessor] = None,
        processor_config: Optional[FrameProcessorConfig] = None,
    ) -> None:
        """
        Attach to a PlaybackEngine, automatically converting emitted DecodedFrames
        to RGBFrames via FrameProcessor and writing them to hardware.
        """
        from keyboard_re.visualizer.processor import FrameProcessor, FrameProcessorConfig

        self._attached_engine = engine
        self._attached_processor = processor or FrameProcessor()
        self._attached_config = processor_config or FrameProcessorConfig()

        def _on_engine_frame(decoded_frame: DecodedFrame) -> None:
            if not self.is_active:
                return

            telem = self._telemetry
            is_telem = telem is not None and telem.enabled

            t_cb_interval = telem.record_callback_interval() if is_telem else 0.0
            t_p0 = time.perf_counter() if is_telem else 0.0
            rgb_frame = self._attached_processor.process_image(
                decoded_frame.image,
                self._attached_config,
            )
            t_p1 = time.perf_counter() if is_telem else 0.0
            t_proc_ms = (t_p1 - t_p0) * 1000.0 if is_telem else 0.0

            self.write_frame(
                rgb_frame,
                t_processor_ms=t_proc_ms,
                t_callback_interval_ms=t_cb_interval,
            )

        engine.on_frame = _on_engine_frame

        # Hook into engine error callback to trigger restore if error occurs
        existing_on_error = engine.on_error

        def _on_engine_error(exc: Exception) -> None:
            logger.error("PlaybackEngine reported error: %s. Restoring RGB states...", exc)
            try:
                self.restore_previous_rgb()
            except Exception as e:
                logger.warning("Failed to restore RGB states on engine error: %s", e)
            if existing_on_error:
                existing_on_error(exc)

        engine.on_error = _on_engine_error

    # -------------------------------------------------------------------------
    # Context Manager
    # -------------------------------------------------------------------------
    def __enter__(self) -> KeyboardRgbOutput:
        self.start_visualizer()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop_visualizer(restore=True)
