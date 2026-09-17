"""
PlaybackEngine: Clock-based timing and media playback controller for Keyboard Visualizer.

Provides:
- Monotonic clock-based timing with speed scaling and zero-jump rate changes.
- Variable frame duration support (GIF timing and Video FPS).
- Frame dropping when downstream processing lags.
- Thread-safe play, pause, resume, stop, seek, and loop controls.
- Injectable Clock abstraction (SystemClock / FakeClock) for deterministic testing.
- Event and frame callbacks executed outside internal locks.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional, Protocol, Union

from keyboard_re.visualizer.decoder import (
    BaseMediaDecoder,
    DecodedFrame,
)

logger = logging.getLogger(__name__)


class PlaybackState(str, Enum):
    """Current state of the playback engine."""
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class Clock(Protocol):
    """Abstract clock interface for deterministic timing and testing."""
    def now(self) -> float:
        """Return current monotonic time in seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for the specified number of seconds."""
        ...

    def wait(self, event: threading.Event, timeout: Optional[float] = None) -> bool:
        """Wait for event with optional timeout in seconds. Return True if event was set."""
        ...


class SystemClock:
    """Default system monotonic clock."""
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def wait(self, event: threading.Event, timeout: Optional[float] = None) -> bool:
        return event.wait(timeout)


class FakeClock:
    """
    Deterministic simulated clock for tests.
    Does not use wall-clock time; advances only when advance() or sleep() is called.
    """
    def __init__(self, initial_time: float = 0.0) -> None:
        self._current_time = float(initial_time)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._current_time

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._current_time += max(0.0, float(seconds))

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def wait(self, event: threading.Event, timeout: Optional[float] = None) -> bool:
        if timeout is not None and timeout > 0:
            self.advance(timeout)
        return event.is_set()


class PlaybackEngine:
    """
    Thread-safe playback and timing engine driving visualizer frame sequences.
    """

    def __init__(
        self,
        decoder: BaseMediaDecoder,
        clock: Optional[Clock] = None,
        run_worker: bool = True,
        on_frame: Optional[Callable[[DecodedFrame], None]] = None,
        on_started: Optional[Callable[[], None]] = None,
        on_paused: Optional[Callable[[], None]] = None,
        on_stopped: Optional[Callable[[], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self._decoder = decoder
        self._clock: Clock = clock or SystemClock()
        self._auto_worker = run_worker

        # Callbacks
        self.on_frame = on_frame
        self.on_started = on_started
        self.on_paused = on_paused
        self.on_stopped = on_stopped
        self.on_finished = on_finished
        self.on_error = on_error

        # Playback configuration
        self._speed: float = 1.0
        self._loop: bool = (decoder.metadata.loop_count == 0)

        # State tracking
        self._lock = threading.Lock()
        self._state: PlaybackState = PlaybackState.STOPPED
        self._media_position: float = 0.0        # Media timestamp in seconds
        self._clock_base: float = 0.0            # Monotonic time when current segment started
        self._current_frame_index: int = 0
        self._current_timestamp: float = 0.0
        self._last_emitted_index: int = -1

        # Performance metrics
        self._emitted_frames: int = 0
        self._dropped_frames: int = 0
        self._playback_start_clock: Optional[float] = None
        self._accumulated_elapsed_wall: float = 0.0

        # Threading controls
        self._shutdown_event = threading.Event()
        self._wake_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        if self._auto_worker:
            self._start_worker()

    # -------------------------------------------------------------------------
    # Public Properties
    # -------------------------------------------------------------------------
    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._state == PlaybackState.PLAYING

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._state == PlaybackState.PAUSED

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            return self._state == PlaybackState.STOPPED

    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return self._state

    @property
    def duration(self) -> Optional[float]:
        return self._decoder.metadata.duration

    @property
    def playback_speed(self) -> float:
        with self._lock:
            return self._speed

    @property
    def loop_enabled(self) -> bool:
        with self._lock:
            return self._loop

    @property
    def current_frame_index(self) -> int:
        with self._lock:
            return self._current_frame_index

    @property
    def current_timestamp(self) -> float:
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                now = self._clock.now()
                return self._media_position + (now - self._clock_base) * self._speed
            return self._media_position

    @property
    def emitted_frames(self) -> int:
        with self._lock:
            return self._emitted_frames

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def elapsed_playback_time(self) -> float:
        with self._lock:
            if self._state == PlaybackState.PLAYING and self._playback_start_clock is not None:
                return self._accumulated_elapsed_wall + (self._clock.now() - self._playback_start_clock)
            return self._accumulated_elapsed_wall

    # -------------------------------------------------------------------------
    # Control Actions
    # -------------------------------------------------------------------------
    def play(self) -> None:
        """Start or resume playback."""
        callback_to_fire = None
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                return

            now = self._clock.now()
            if self._state == PlaybackState.STOPPED:
                self._media_position = 0.0
                self._last_emitted_index = -1
                self._emitted_frames = 0
                self._dropped_frames = 0
                self._accumulated_elapsed_wall = 0.0

            self._clock_base = now
            self._playback_start_clock = now
            self._state = PlaybackState.PLAYING
            callback_to_fire = self.on_started

        # Wake worker thread
        self._wake_event.set()

        if callback_to_fire:
            self._safe_invoke(callback_to_fire)

    def pause(self) -> None:
        """Pause playback, preserving current media position."""
        callback_to_fire = None
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return

            now = self._clock.now()
            self._media_position += (now - self._clock_base) * self._speed
            if self._playback_start_clock is not None:
                self._accumulated_elapsed_wall += (now - self._playback_start_clock)
                self._playback_start_clock = None

            self._state = PlaybackState.PAUSED
            callback_to_fire = self.on_paused

        # Trigger wake to reset wait condition
        self._wake_event.set()

        if callback_to_fire:
            self._safe_invoke(callback_to_fire)

    def stop(self) -> None:
        """Stop playback and rewind to beginning."""
        callback_to_fire = None
        with self._lock:
            if self._state == PlaybackState.STOPPED:
                return

            self._state = PlaybackState.STOPPED
            self._media_position = 0.0
            self._current_frame_index = 0
            self._current_timestamp = 0.0
            self._last_emitted_index = -1
            self._playback_start_clock = None
            callback_to_fire = self.on_stopped

        self._wake_event.set()

        if callback_to_fire:
            self._safe_invoke(callback_to_fire)

    def seek(self, target_timestamp: float) -> None:
        """
        Seek to the specified timestamp in seconds.
        Clamps to valid media range [0.0, duration] if duration is known.
        """
        t = max(0.0, float(target_timestamp))
        dur = self.duration
        if dur is not None and dur > 0:
            t = min(t, dur)

        with self._lock:
            self._media_position = t
            if self._state == PlaybackState.PLAYING:
                self._clock_base = self._clock.now()

        # Immediately fetch and emit frame at seek position
        frame = self._decoder.get_frame_at_time(t)
        if frame is not None:
            with self._lock:
                self._current_frame_index = frame.frame_index
                self._current_timestamp = frame.timestamp
                self._last_emitted_index = frame.frame_index
            self._notify_frame(frame)

        self._wake_event.set()

    def set_speed(self, multiplier: float) -> None:
        """
        Set playback speed multiplier (e.g. 0.5x, 1.0x, 2.0x).
        Guarantees seamless rate transition without jumping media position.
        """
        val = float(multiplier)
        if val <= 0.0:
            raise ValueError(f"Playback speed must be positive, got {multiplier}")

        with self._lock:
            if self._state == PlaybackState.PLAYING:
                now = self._clock.now()
                # Accumulate elapsed media time under previous speed
                self._media_position += (now - self._clock_base) * self._speed
                self._clock_base = now
            self._speed = val

        self._wake_event.set()

    def set_loop(self, enabled: bool) -> None:
        """Enable or disable infinite looping."""
        with self._lock:
            self._loop = bool(enabled)

    def close(self) -> None:
        """Shut down worker thread and release decoder resources."""
        self._shutdown_event.set()
        self._wake_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._decoder.close()

    # -------------------------------------------------------------------------
    # Deterministic Step Engine (for testing & synchronous operation)
    # -------------------------------------------------------------------------
    def step(self, delta_seconds: float = 0.0) -> Optional[DecodedFrame]:
        """
        Advance media time by delta_seconds and emit frame if active frame changed.
        Used for deterministic unit testing without wall-clock waits.
        """
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return None

            # Advance clock if FakeClock
            if hasattr(self._clock, "advance"):
                self._clock.advance(delta_seconds)

            current_pos = self._media_position + (self._clock.now() - self._clock_base) * self._speed

        return self._process_tick(current_pos)

    # -------------------------------------------------------------------------
    # Worker Thread & Internal Loop
    # -------------------------------------------------------------------------
    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="VisualizerPlaybackWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Main background worker timing loop."""
        while not self._shutdown_event.is_set():
            with self._lock:
                state = self._state
                pos = self._media_position
                base = self._clock_base
                speed = self._speed

            if state != PlaybackState.PLAYING:
                # Wait until play() or shutdown is triggered
                self._wake_event.wait(timeout=0.1)
                self._wake_event.clear()
                continue

            # Compute current media timestamp based on monotonic clock
            now = self._clock.now()
            target_media_time = pos + (now - base) * speed

            # Process this timing tick
            frame = self._process_tick(target_media_time)

            # Calculate sleep until next frame
            sleep_duration = 0.01  # Default fallback sleep (10ms)
            if frame is not None:
                next_media_ts = frame.timestamp + frame.duration
                rem_media = max(0.0, next_media_ts - target_media_time)
                sleep_duration = rem_media / speed if speed > 0 else 0.01

            # Sleep responsiveness: wait for sleep_duration or wake event (max 50ms)
            wait_time = min(sleep_duration, 0.05)
            self._clock.wait(self._wake_event, timeout=wait_time)
            self._wake_event.clear()

    def _process_tick(self, target_media_time: float) -> Optional[DecodedFrame]:
        """
        Core timing logic: evaluates target_media_time, handles looping / end-of-media,
        fetches the appropriate frame, detects dropped frames, and triggers callbacks.
        """
        dur = self.duration
        with self._lock:
            loop_enabled = self._loop

        # 1. Handle end-of-media
        if dur is not None and target_media_time >= dur:
            if loop_enabled:
                # Loop back to 0.0
                with self._lock:
                    self._media_position = 0.0
                    self._clock_base = self._clock.now()
                    self._last_emitted_index = -1
                target_media_time = 0.0
            else:
                # Playback complete
                finish_callback = None
                with self._lock:
                    if self._state == PlaybackState.PLAYING:
                        self._state = PlaybackState.STOPPED
                        self._media_position = dur
                        finish_callback = self.on_finished
                if finish_callback:
                    self._safe_invoke(finish_callback)
                return None

        # 2. Fetch frame corresponding to target_media_time
        try:
            frame = self._decoder.get_frame_at_time(target_media_time)
        except Exception as e:
            logger.error("Decoder error in playback loop: %s", e)
            error_callback = None
            with self._lock:
                self._state = PlaybackState.STOPPED
                error_callback = self.on_error
            if error_callback:
                self._safe_invoke(error_callback, e)
            return None

        if frame is None:
            return None

        # 3. Check if frame changed
        with self._lock:
            last_idx = self._last_emitted_index
            if frame.frame_index == last_idx:
                # Still within the current frame's display duration
                return frame

            # Frame Dropping Detection:
            # If target frame is ahead of expected next frame, record dropped frames
            if last_idx >= 0 and frame.frame_index > last_idx + 1:
                dropped = frame.frame_index - (last_idx + 1)
                self._dropped_frames += dropped

            self._current_frame_index = frame.frame_index
            self._current_timestamp = frame.timestamp
            self._last_emitted_index = frame.frame_index
            self._emitted_frames += 1

        # 4. Notify consumer outside state lock
        self._notify_frame(frame)
        return frame

    def _notify_frame(self, frame: DecodedFrame) -> None:
        if self.on_frame:
            self._safe_invoke(self.on_frame, frame)

    def _safe_invoke(self, callback: Callable, *args) -> None:
        try:
            callback(*args)
        except Exception as e:
            logger.exception("Exception in PlaybackEngine callback: %s", e)
            if self.on_error and callback != self.on_error:
                try:
                    self.on_error(e)
                except Exception:
                    pass
