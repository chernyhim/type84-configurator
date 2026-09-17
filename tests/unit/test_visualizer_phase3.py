"""
Unit tests for Keyboard Visualizer Phase 3: PlaybackEngine.

Tests verify:
- Initial state, play, pause, resume, stop, and seek controls.
- Speed scaling (0.25x, 0.5x, 1.0x, 2.0x) with zero position jump.
- Loop and end-of-media handling.
- Variable frame durations (e.g. GIF timing: 100ms, 50ms, 200ms).
- Frame dropping when timing lags behind.
- Deterministic timing via FakeClock.
- Thread-safe worker thread shutdown.
- Callback sequence and exception safety.
- Subsystem isolation (zero imports of closed modules).
"""

from __future__ import annotations

import ast
import time
import unittest
from pathlib import Path
from typing import Iterator, List, Optional

from PIL import Image

from keyboard_re.visualizer import (
    BaseMediaDecoder,
    DecodedFrame,
    FakeClock,
    MediaMetadata,
    PlaybackEngine,
    PlaybackState,
)


class MockMediaDecoder(BaseMediaDecoder):
    """Deterministic in-memory media decoder for test scenarios."""

    def __init__(
        self,
        frame_durations: List[float],
        loop_count: int = 1,
        width: int = 64,
        height: int = 48,
    ) -> None:
        self.frame_durations = list(frame_durations)
        self._loop_count = loop_count
        self._width = width
        self._height = height
        self._closed = False
        self.fail_on_fetch = False

        total_dur = sum(self.frame_durations)
        avg_fps = len(self.frame_durations) / total_dur if total_dur > 0 else 10.0

        self._meta = MediaMetadata(
            media_type="mock",
            width=width,
            height=height,
            fps=avg_fps,
            total_frames=len(self.frame_durations),
            duration=total_dur,
            loop_count=loop_count,
        )

    @property
    def metadata(self) -> MediaMetadata:
        return self._meta

    def get_frame_at_time(self, target_time: float) -> Optional[DecodedFrame]:
        if self.fail_on_fetch:
            raise RuntimeError("Simulated decoder hardware failure")

        if not self.frame_durations:
            return None

        total_dur = self._meta.duration or 0.0
        t = max(0.0, min(target_time, max(0.0, total_dur - 0.0001)))

        cum_time = 0.0
        for idx, dur in enumerate(self.frame_durations):
            if cum_time + dur > t or idx == len(self.frame_durations) - 1:
                return self.get_frame_by_index(idx, timestamp=cum_time)
            cum_time += dur

        return self.get_frame_by_index(len(self.frame_durations) - 1)

    def get_frame_by_index(self, frame_index: int, timestamp: Optional[float] = None) -> Optional[DecodedFrame]:
        if self.fail_on_fetch:
            raise RuntimeError("Simulated decoder hardware failure")

        idx = max(0, min(frame_index, len(self.frame_durations) - 1))
        dur = self.frame_durations[idx]
        ts = timestamp if timestamp is not None else sum(self.frame_durations[:idx])

        # Solid color encoded by frame index
        img = Image.new("RGB", (self._width, self._height), (idx * 20, 100, 150))
        return DecodedFrame(
            image=img,
            frame_index=idx,
            timestamp=ts,
            duration=dur,
        )

    def __iter__(self) -> Iterator[DecodedFrame]:
        cum = 0.0
        for idx, dur in enumerate(self.frame_durations):
            yield self.get_frame_by_index(idx, timestamp=cum)
            cum += dur

    def close(self) -> None:
        self._closed = True


class TestVisualizerPhase3(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(initial_time=100.0)

    # -------------------------------------------------------------------------
    # 1. Initial State & Controls
    # -------------------------------------------------------------------------
    def test_initial_state(self) -> None:
        """Verify PlaybackEngine initial default state."""
        decoder = MockMediaDecoder([0.1, 0.1, 0.1])
        engine = PlaybackEngine(decoder, clock=self.clock, run_worker=False)

        self.assertTrue(engine.is_stopped)
        self.assertFalse(engine.is_playing)
        self.assertFalse(engine.is_paused)
        self.assertEqual(engine.current_frame_index, 0)
        self.assertEqual(engine.current_timestamp, 0.0)
        self.assertAlmostEqual(engine.duration, 0.3)
        self.assertEqual(engine.playback_speed, 1.0)
        self.assertFalse(engine.loop_enabled)
        self.assertEqual(engine.emitted_frames, 0)
        self.assertEqual(engine.dropped_frames, 0)
        engine.close()

    def test_play_pause_resume_stop_lifecycle(self) -> None:
        """Verify standard play, pause, resume, and stop state transitions."""
        events: List[str] = []
        decoder = MockMediaDecoder([0.1, 0.1, 0.1])
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_started=lambda: events.append("started"),
            on_paused=lambda: events.append("paused"),
            on_stopped=lambda: events.append("stopped"),
        )

        # 1. Play
        engine.play()
        self.assertTrue(engine.is_playing)
        self.assertEqual(events, ["started"])

        # Advance time by 0.05s (still within frame 0)
        engine.step(0.05)
        self.assertAlmostEqual(engine.current_timestamp, 0.05)

        # 2. Pause
        engine.pause()
        self.assertTrue(engine.is_paused)
        self.assertEqual(events, ["started", "paused"])
        self.assertAlmostEqual(engine.current_timestamp, 0.05)

        # Time passes while paused (0.5s) -> position must remain frozen!
        self.clock.advance(0.5)
        self.assertAlmostEqual(engine.current_timestamp, 0.05)

        # 3. Resume (play())
        engine.play()
        self.assertTrue(engine.is_playing)
        self.assertEqual(events, ["started", "paused", "started"])
        # Position resumes from 0.05s
        self.assertAlmostEqual(engine.current_timestamp, 0.05)

        # 4. Stop
        engine.stop()
        self.assertTrue(engine.is_stopped)
        self.assertEqual(engine.current_timestamp, 0.0)
        self.assertEqual(engine.current_frame_index, 0)
        self.assertEqual(events, ["started", "paused", "started", "stopped"])

        engine.close()

    # -------------------------------------------------------------------------
    # 2. Variable Frame Durations (GIF-like)
    # -------------------------------------------------------------------------
    def test_variable_frame_durations(self) -> None:
        """Verify variable frame durations (100ms, 50ms, 200ms) are emitted at correct timestamps."""
        emitted_indices: List[int] = []
        # Frame 0: 0.00..0.10s (100ms)
        # Frame 1: 0.10..0.15s (50ms)
        # Frame 2: 0.15..0.35s (200ms)
        decoder = MockMediaDecoder([0.10, 0.05, 0.20])
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_frame=lambda f: emitted_indices.append(f.frame_index),
        )

        engine.play()

        # Tick 0: initial frame 0 emitted
        engine.step(0.0)
        self.assertEqual(emitted_indices, [0])

        # Tick 1: at t=0.06s (within frame 0, no new frame emitted)
        engine.step(0.06)
        self.assertEqual(emitted_indices, [0])

        # Tick 2: at t=0.11s (entered frame 1, 50ms duration)
        engine.step(0.05)
        self.assertEqual(emitted_indices, [0, 1])

        # Tick 3: at t=0.16s (entered frame 2, 200ms duration)
        engine.step(0.05)
        self.assertEqual(emitted_indices, [0, 1, 2])

        engine.close()

    # -------------------------------------------------------------------------
    # 3. Frame Dropping under Processing Lag
    # -------------------------------------------------------------------------
    def test_frame_dropping_when_lagging(self) -> None:
        """When clock leaps ahead, engine drops intermediate frames and updates dropped_frames count."""
        emitted_frames: List[int] = []
        # 10 frames of 0.02s each (total 0.20s = 50 FPS)
        decoder = MockMediaDecoder([0.02] * 10)
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_frame=lambda f: emitted_frames.append(f.frame_index),
        )

        engine.play()
        engine.step(0.0)
        self.assertEqual(emitted_frames, [0])

        # Simulate a heavy 0.10s stall: jumps from t=0.0 to t=0.105s (skipping frames 1, 2, 3, 4)
        engine.step(0.105)

        # Should jump straight to frame 5 (which spans 0.10..0.12s)
        self.assertEqual(emitted_frames, [0, 5])
        # Dropped frames 1, 2, 3, 4 = 4 dropped
        self.assertEqual(engine.dropped_frames, 4)
        self.assertEqual(engine.emitted_frames, 2)

        engine.close()

    # -------------------------------------------------------------------------
    # 4. Playback Speed Scaling (0.5x, 1x, 2x) & Zero Position Jump
    # -------------------------------------------------------------------------
    def test_playback_speed_scaling_and_seamless_change(self) -> None:
        """Verify speed multipliers and seamless rate switching without position jumps."""
        decoder = MockMediaDecoder([0.1] * 10)  # total 1.0s
        engine = PlaybackEngine(decoder, clock=self.clock, run_worker=False)

        engine.play()

        # At speed 1.0x, advance clock by 0.2s -> media time is 0.2s
        engine.step(0.2)
        self.assertAlmostEqual(engine.current_timestamp, 0.2, places=3)

        # Change speed to 2.0x (double speed)
        engine.set_speed(2.0)
        # Position immediately after speed change must NOT jump!
        self.assertAlmostEqual(engine.current_timestamp, 0.2, places=3)

        # Advance clock by 0.1s at 2x speed -> media advances by 0.2s -> position = 0.4s
        engine.step(0.1)
        self.assertAlmostEqual(engine.current_timestamp, 0.4, places=3)

        # Change speed to 0.5x (half speed)
        engine.set_speed(0.5)
        self.assertAlmostEqual(engine.current_timestamp, 0.4, places=3)

        # Advance clock by 0.2s at 0.5x speed -> media advances by 0.1s -> position = 0.5s
        engine.step(0.2)
        self.assertAlmostEqual(engine.current_timestamp, 0.5, places=3)

        engine.close()

    # -------------------------------------------------------------------------
    # 5. Seek Controls
    # -------------------------------------------------------------------------
    def test_seek_clamping_and_immediate_frame_emission(self) -> None:
        """Verify seek clamps to [0, duration] and immediately emits the target frame."""
        emitted: List[int] = []
        decoder = MockMediaDecoder([0.1, 0.1, 0.1, 0.1, 0.1])  # 5 frames, total 0.5s
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_frame=lambda f: emitted.append(f.frame_index),
        )

        engine.play()
        engine.step(0.0)
        self.assertEqual(emitted, [0])

        # Seek forward to 0.35s (frame 3 spans 0.3..0.4s)
        engine.seek(0.35)
        self.assertAlmostEqual(engine.current_timestamp, 0.35, places=3)
        self.assertEqual(engine.current_frame_index, 3)
        self.assertIn(3, emitted)

        # Seek beyond duration (0.5s) -> clamped to 0.5s
        engine.seek(10.0)
        self.assertAlmostEqual(engine.current_timestamp, 0.5, places=3)

        # Seek below 0 -> clamped to 0.0s
        engine.seek(-5.0)
        self.assertAlmostEqual(engine.current_timestamp, 0.0, places=3)
        self.assertEqual(engine.current_frame_index, 0)

        engine.close()

    # -------------------------------------------------------------------------
    # 6. Loop vs Non-Loop End of Media
    # -------------------------------------------------------------------------
    def test_end_of_media_without_loop(self) -> None:
        """When loop=False, engine stops at duration and fires on_finished."""
        finished = []
        decoder = MockMediaDecoder([0.1, 0.1])  # total 0.2s
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_finished=lambda: finished.append(True),
        )
        engine.set_loop(False)
        engine.play()

        # Step beyond end of media (0.25s)
        engine.step(0.25)
        self.assertTrue(engine.is_stopped)
        self.assertEqual(finished, [True])

        engine.close()

    def test_loop_enabled_wraps_to_start(self) -> None:
        """When loop=True, engine wraps back to start seamlessly."""
        emitted: List[int] = []
        decoder = MockMediaDecoder([0.1, 0.1])  # frames 0 and 1 (0.2s total)
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_frame=lambda f: emitted.append(f.frame_index),
        )
        engine.set_loop(True)
        engine.play()

        # Frame 0
        engine.step(0.0)
        self.assertEqual(emitted, [0])

        # Frame 1
        engine.step(0.12)
        self.assertEqual(emitted, [0, 1])

        # Step past end (0.22s) -> loops back to Frame 0
        engine.step(0.10)
        self.assertEqual(emitted, [0, 1, 0])
        self.assertTrue(engine.is_playing)

        engine.close()

    # -------------------------------------------------------------------------
    # 7. Error Handling & Recovery
    # -------------------------------------------------------------------------
    def test_decoder_error_handling(self) -> None:
        """Decoder exceptions trigger on_error and cleanly stop the engine."""
        errors: List[Exception] = []
        decoder = MockMediaDecoder([0.1, 0.1])
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_error=lambda e: errors.append(e),
        )

        engine.play()
        decoder.fail_on_fetch = True

        engine.step(0.05)
        self.assertTrue(engine.is_stopped)
        self.assertEqual(len(errors), 1)
        self.assertIn("Simulated decoder hardware failure", str(errors[0]))

        engine.close()

    def test_callback_exception_safety(self) -> None:
        """Faulty user callback must not crash the engine."""
        def faulty_callback(f):
            raise ValueError("Crashing user callback")

        decoder = MockMediaDecoder([0.1, 0.1])
        engine = PlaybackEngine(
            decoder,
            clock=self.clock,
            run_worker=False,
            on_frame=faulty_callback,
        )

        engine.play()
        # Should not raise exception
        engine.step(0.0)
        self.assertTrue(engine.is_playing)

        engine.close()

    # -------------------------------------------------------------------------
    # 8. Threaded Worker Lifecycle
    # -------------------------------------------------------------------------
    def test_threaded_worker_clean_shutdown(self) -> None:
        """Verify background worker thread starts and shuts down cleanly on close()."""
        decoder = MockMediaDecoder([0.05, 0.05, 0.05])
        engine = PlaybackEngine(decoder, run_worker=True)

        engine.play()
        self.assertTrue(engine.is_playing)
        time.sleep(0.05)

        engine.pause()
        self.assertTrue(engine.is_paused)

        engine.close()
        self.assertFalse(engine._worker_thread.is_alive())
        self.assertTrue(decoder._closed)

    # -------------------------------------------------------------------------
    # 9. Subsystem Isolation
    # -------------------------------------------------------------------------
    def test_subsystem_isolation(self) -> None:
        """Verify visualizer package does not import closed hardware modules."""
        visualizer_dir = Path(__file__).resolve().parents[2] / "src" / "keyboard_re" / "visualizer"
        forbidden_subsystems = ["hall", "remap", "dks", "macro", "game_mode"]

        for py_file in visualizer_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for term in forbidden_subsystems:
                            self.assertNotIn(term, alias.name.lower())
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for term in forbidden_subsystems:
                        self.assertNotIn(term, mod.lower())


if __name__ == "__main__":
    unittest.main()
