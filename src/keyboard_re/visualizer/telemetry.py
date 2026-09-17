"""
Performance Telemetry for Keyboard Visualizer Pipeline.

Measures precise timings for:
- Decoder / frame acquisition
- PlaybackEngine callback intervals
- FrameProcessor processing time
- RGBFrame serialization (to_led_buffer)
- KeyboardRgbOutput.write_frame() total time
- Per-chunk HID send_report latency
- Per-chunk receive_report / ACK latency
- Full cycle per AA24 chunk
- Full cycle of all 10 AA24 chunks
- Real output FPS and dropped frames count
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChunkTiming:
    """Latency metrics for a single AA24 write chunk."""
    chunk_index: int
    address: int
    send_ms: float
    ack_ms: float
    total_ms: float


@dataclass
class FrameTelemetryRecord:
    """Comprehensive timing record for one visualizer frame."""
    frame_index: int
    timestamp_sec: float = field(default_factory=time.monotonic)
    t_decoder_ms: float = 0.0
    t_callback_interval_ms: float = 0.0
    t_processor_ms: float = 0.0
    t_serialize_ms: float = 0.0
    t_write_frame_ms: float = 0.0
    t_10_chunks_ms: float = 0.0
    chunk_timings: List[ChunkTiming] = field(default_factory=list)


class VisualizerTelemetry:
    """
    Thread-safe performance telemetry collector.
    Can be enabled or disabled dynamically with negligible overhead when disabled.
    """

    def __init__(self, enabled: bool = False, max_history: int = 1000) -> None:
        self.enabled = bool(enabled)
        self.max_history = int(max_history)
        self._lock = threading.RLock()
        self._records: List[FrameTelemetryRecord] = []
        self._last_callback_time: Optional[float] = None
        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None
        self._frames_sent: int = 0
        self._frames_dropped: int = 0

    def reset(self) -> None:
        """Clear all collected records and reset counters."""
        with self._lock:
            self._records.clear()
            self._last_callback_time = None
            self._start_time = None
            self._stop_time = None
            self._frames_sent = 0
            self._frames_dropped = 0

    def start_session(self) -> None:
        """Mark start of a benchmarking / playback session."""
        with self._lock:
            self.reset()
            self._start_time = time.perf_counter()

    def stop_session(self) -> None:
        """Mark end of a benchmarking / playback session."""
        with self._lock:
            self._stop_time = time.perf_counter()

    def record_callback_interval(self) -> float:
        """Record the interval since previous frame callback in ms."""
        if not self.enabled:
            return 0.0
        now = time.perf_counter()
        with self._lock:
            if self._last_callback_time is None:
                interval_ms = 0.0
            else:
                interval_ms = (now - self._last_callback_time) * 1000.0
            self._last_callback_time = now
            return interval_ms

    def record_frame(self, record: FrameTelemetryRecord) -> None:
        """Record a completed frame's telemetry."""
        if not self.enabled:
            return
        with self._lock:
            self._frames_sent += 1
            if len(self._records) < self.max_history:
                self._records.append(record)

    def record_dropped_frames(self, count: int) -> None:
        """Update count of dropped frames."""
        if not self.enabled:
            return
        with self._lock:
            self._frames_dropped += count

    def get_summary(self) -> Dict[str, Any]:
        """Compute statistical summary of collected telemetry."""
        with self._lock:
            records = list(self._records)
            sent = self._frames_sent
            dropped = self._frames_dropped
            start_t = self._start_time
            stop_t = self._stop_time or time.perf_counter()

        elapsed_sec = (stop_t - start_t) if start_t else 0.0
        fps = (sent / elapsed_sec) if elapsed_sec > 0 else 0.0

        if not records:
            return {
                "frames_sent": sent,
                "frames_dropped": dropped,
                "elapsed_sec": elapsed_sec,
                "output_fps": fps,
                "has_records": False,
            }

        def _stats(values: List[float]) -> Dict[str, float]:
            if not values:
                return {"min": 0.0, "avg": 0.0, "max": 0.0}
            return {
                "min": round(min(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "max": round(max(values), 2),
            }

        decoder_times = [r.t_decoder_ms for r in records if r.t_decoder_ms > 0]
        callback_intervals = [r.t_callback_interval_ms for r in records if r.t_callback_interval_ms > 0]
        processor_times = [r.t_processor_ms for r in records]
        serialize_times = [r.t_serialize_ms for r in records]
        write_times = [r.t_write_frame_ms for r in records if r.t_write_frame_ms > 0]
        ten_chunks_times = [r.t_10_chunks_ms for r in records if r.t_10_chunks_ms > 0]

        all_chunk_sends: List[float] = []
        all_chunk_acks: List[float] = []
        all_chunk_totals: List[float] = []
        for r in records:
            for c in r.chunk_timings:
                all_chunk_sends.append(c.send_ms)
                all_chunk_acks.append(c.ack_ms)
                all_chunk_totals.append(c.total_ms)

        return {
            "has_records": True,
            "frames_sent": sent,
            "frames_dropped": dropped,
            "elapsed_sec": round(elapsed_sec, 2),
            "output_fps": round(fps, 2),
            "decoder_ms": _stats(decoder_times),
            "callback_interval_ms": _stats(callback_intervals),
            "processor_ms": _stats(processor_times),
            "serialize_ms": _stats(serialize_times),
            "write_frame_ms": _stats(write_times),
            "ten_chunks_total_ms": _stats(ten_chunks_times),
            "chunk_send_ms": _stats(all_chunk_sends),
            "chunk_ack_ms": _stats(all_chunk_acks),
            "chunk_cycle_ms": _stats(all_chunk_totals),
        }

    def format_report(self) -> str:
        """Format a human-readable telemetry summary report."""
        s = self.get_summary()
        if not s.get("has_records"):
            return (
                f"Visualizer Telemetry Summary:\n"
                f"  Frames sent: {s['frames_sent']}, Dropped: {s['frames_dropped']}\n"
                f"  Elapsed: {s['elapsed_sec']:.2f}s, Output FPS: {s['output_fps']:.2f}\n"
                f"  (No detailed frame timing records captured)"
            )

        lines = [
            "==================================================",
            "VISUALIZER PERFORMANCE TELEMETRY REPORT",
            "==================================================",
            f"Frames Sent:       {s['frames_sent']}",
            f"Frames Dropped:    {s['frames_dropped']}",
            f"Elapsed Time:      {s['elapsed_sec']:.2f}s",
            f"Actual Output FPS: {s['output_fps']:.2f} fps",
            "--------------------------------------------------",
            "LATENCY BREAKDOWN (min / avg / max ms):",
            f"  1. Decoder Fetch:         {s['decoder_ms']['min']:>6.2f} / {s['decoder_ms']['avg']:>6.2f} / {s['decoder_ms']['max']:>6.2f} ms",
            f"  2. Callback Interval:     {s['callback_interval_ms']['min']:>6.2f} / {s['callback_interval_ms']['avg']:>6.2f} / {s['callback_interval_ms']['max']:>6.2f} ms",
            f"  3. FrameProcessor:        {s['processor_ms']['min']:>6.2f} / {s['processor_ms']['avg']:>6.2f} / {s['processor_ms']['max']:>6.2f} ms",
            f"  4. RGBFrame Serialize:    {s['serialize_ms']['min']:>6.2f} / {s['serialize_ms']['avg']:>6.2f} / {s['serialize_ms']['max']:>6.2f} ms",
            f"  5. Keyboard write_frame:  {s['write_frame_ms']['min']:>6.2f} / {s['write_frame_ms']['avg']:>6.2f} / {s['write_frame_ms']['max']:>6.2f} ms",
            "--------------------------------------------------",
            "HID AA24 USB PROTOCOL BREAKDOWN:",
            f"  - Per-chunk Send Report:  {s['chunk_send_ms']['min']:>6.2f} / {s['chunk_send_ms']['avg']:>6.2f} / {s['chunk_send_ms']['max']:>6.2f} ms",
            f"  - Per-chunk ACK Latency:  {s['chunk_ack_ms']['min']:>6.2f} / {s['chunk_ack_ms']['avg']:>6.2f} / {s['chunk_ack_ms']['max']:>6.2f} ms",
            f"  - Full Cycle per Chunk:   {s['chunk_cycle_ms']['min']:>6.2f} / {s['chunk_cycle_ms']['avg']:>6.2f} / {s['chunk_cycle_ms']['max']:>6.2f} ms",
            f"  - Full 10 AA24 Chunks:    {s['ten_chunks_total_ms']['min']:>6.2f} / {s['ten_chunks_total_ms']['avg']:>6.2f} / {s['ten_chunks_total_ms']['max']:>6.2f} ms",
            "==================================================",
        ]
        return "\n".join(lines)


# Global default telemetry instance
_GLOBAL_TELEMETRY = VisualizerTelemetry(enabled=False)


def get_global_telemetry() -> VisualizerTelemetry:
    """Return the global visualizer telemetry instance."""
    return _GLOBAL_TELEMETRY
