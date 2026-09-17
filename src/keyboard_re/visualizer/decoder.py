"""
Streaming Media Decoders for Keyboard Visualizer.

Provides streaming decoding for animated GIFs and video files (MP4, AVI, etc.)
with per-frame timing metadata, without buffering entire streams into memory.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image, ImageSequence


class VisualizerDecoderError(Exception):
    """Base exception for visualizer decoder errors."""
    pass


class UnsupportedFormatError(VisualizerDecoderError):
    """Raised when the input file format is not supported."""
    pass


class CorruptMediaError(VisualizerDecoderError):
    """Raised when a media file cannot be opened or is corrupted."""
    pass


@dataclass(frozen=True)
class MediaMetadata:
    """Metadata describing a media source."""
    media_type: str                         # "gif", "video", "static_image"
    width: int
    height: int
    fps: Optional[float]                    # None if variable frame duration (e.g. GIF)
    total_frames: Optional[int]             # None if unknown / infinite
    duration: Optional[float]               # Total duration in seconds (if known)
    loop_count: int = 0                     # 0 = infinite loop, 1 = play once, N = loop N times


@dataclass(frozen=True)
class DecodedFrame:
    """
    Normalized video or GIF frame yielded by decoders.
    Fully decoupled from keyboard mapping; contains standardized RGB PIL Image and timing.
    """
    image: Image.Image                      # Standard RGB PIL Image
    frame_index: int                        # 0-indexed frame sequence number
    timestamp: float                        # Presentation timestamp in seconds from stream start
    duration: float                         # Duration of this frame in seconds


class BaseMediaDecoder(ABC):
    """Abstract base class for streaming media decoders."""

    @property
    @abstractmethod
    def metadata(self) -> MediaMetadata:
        """Return metadata for the media stream."""
        ...

    @abstractmethod
    def __iter__(self) -> Iterator[DecodedFrame]:
        """Iterate over frames sequentially."""
        ...

    @abstractmethod
    def get_frame_at_time(self, target_time: float) -> Optional[DecodedFrame]:
        """Return the DecodedFrame covering target_time in seconds."""
        ...

    @abstractmethod
    def get_frame_by_index(self, frame_index: int) -> Optional[DecodedFrame]:
        """Fetch a specific frame index and return as DecodedFrame."""
        ...

    def close(self) -> None:
        """Release underlying resources (file handles, decoders)."""
        pass

    def __enter__(self) -> BaseMediaDecoder:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class GifDecoder(BaseMediaDecoder):
    """
    Streaming decoder for animated and static GIF files using Pillow.
    Accurately tracks per-frame durations and loop metadata without assuming a fixed FPS.
    """

    DEFAULT_FRAME_DURATION_MS: float = 100.0  # Fallback: 100ms (10 FPS) if duration missing/0

    def __init__(self, file_path: Union[str, Path]) -> None:
        self.path = Path(file_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"GIF file not found: {self.path}")

        try:
            self._img = Image.open(self.path)
            self._img.load()
        except Exception as e:
            raise CorruptMediaError(f"Failed to open GIF file '{self.path}': {e}") from e

        if self._img.format != "GIF":
            self.close()
            raise UnsupportedFormatError(f"File '{self.path}' is not a GIF (format: {self._img.format})")

        # Inspect frames and durations
        self._n_frames = getattr(self._img, "n_frames", 1)
        self._is_animated = getattr(self._img, "is_animated", False) and self._n_frames > 1

        # Extract per-frame durations to compute total duration
        self._frame_durations: list[float] = []
        for idx in range(self._n_frames):
            try:
                self._img.seek(idx)
                dur_ms = self._img.info.get("duration", self.DEFAULT_FRAME_DURATION_MS)
                if dur_ms is None or dur_ms <= 0:
                    dur_ms = self.DEFAULT_FRAME_DURATION_MS
                self._frame_durations.append(float(dur_ms) / 1000.0)
            except EOFError:
                break

        # Reset to first frame
        self._img.seek(0)

        total_dur = sum(self._frame_durations) if self._frame_durations else None
        avg_fps = (len(self._frame_durations) / total_dur) if total_dur and total_dur > 0 else None

        # Loop metadata: Netscape extension 'loop' = 0 means infinite loop
        # Default loop: 0 (infinite) for animated GIF if not specified, 1 for static
        loop_meta = self._img.info.get("loop", 0 if self._is_animated else 1)

        self._metadata = MediaMetadata(
            media_type="gif",
            width=self._img.width,
            height=self._img.height,
            fps=avg_fps,
            total_frames=len(self._frame_durations),
            duration=total_dur,
            loop_count=loop_meta,
        )

    @property
    def metadata(self) -> MediaMetadata:
        return self._metadata

    def get_frame_at_time(self, target_time: float) -> Optional[DecodedFrame]:
        """Return the DecodedFrame covering target_time in seconds."""
        if not self._frame_durations:
            return None
        total_dur = self._metadata.duration or sum(self._frame_durations)
        t = max(0.0, min(target_time, max(0.0, total_dur - 0.0001)))

        cum_time = 0.0
        target_idx = 0
        for idx, dur in enumerate(self._frame_durations):
            if cum_time + dur > t or idx == len(self._frame_durations) - 1:
                target_idx = idx
                break
            cum_time += dur

        return self.get_frame_by_index(target_idx, timestamp=cum_time)

    def get_frame_by_index(self, frame_index: int, timestamp: Optional[float] = None) -> Optional[DecodedFrame]:
        """Fetch a specific frame index and return as DecodedFrame."""
        total = self._metadata.total_frames or 1
        idx = max(0, min(frame_index, total - 1))
        try:
            self._img.seek(idx)
        except Exception:
            return None
        frame_rgb = self._img.convert("RGB")
        dur = self._frame_durations[idx] if idx < len(self._frame_durations) else (self.DEFAULT_FRAME_DURATION_MS / 1000.0)
        ts = timestamp if timestamp is not None else sum(self._frame_durations[:idx])
        return DecodedFrame(image=frame_rgb, frame_index=idx, timestamp=ts, duration=dur)

    def __iter__(self) -> Iterator[DecodedFrame]:
        """
        Stream frames sequentially. Handles palette conversion and transparency.
        """
        curr_time = 0.0
        for idx in range(self._metadata.total_frames or 1):
            try:
                self._img.seek(idx)
            except (EOFError, ValueError):
                break

            # Convert to RGB mode
            frame_rgb = self._img.convert("RGB")

            dur = self._frame_durations[idx] if idx < len(self._frame_durations) else (self.DEFAULT_FRAME_DURATION_MS / 1000.0)

            yield DecodedFrame(
                image=frame_rgb,
                frame_index=idx,
                timestamp=curr_time,
                duration=dur,
            )
            curr_time += dur

    def close(self) -> None:
        if hasattr(self, "_img") and self._img:
            try:
                self._img.close()
            except Exception:
                pass


class VideoDecoder(BaseMediaDecoder):
    """
    Streaming video decoder using OpenCV (cv2.VideoCapture).
    Reads frames sequentially on-the-fly without buffering the video into RAM.
    """

    SUPPORTED_EXTENSIONS = frozenset({".mp4", ".avi", ".webm", ".mkv", ".mov"})

    def __init__(self, file_path: Union[str, Path]) -> None:
        self.path = Path(file_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"Video file not found: {self.path}")

        ext = self.path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"Unsupported video extension '{ext}'. Supported: {sorted(self.SUPPORTED_EXTENSIONS)}"
            )

        self._cap = cv2.VideoCapture(str(self.path))
        if not self._cap.isOpened():
            raise CorruptMediaError(f"OpenCV failed to open video file: '{self.path}'")

        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(self._cap.get(cv2.CAP_PROP_FPS))
        if fps <= 0 or np.isnan(fps):
            fps = 30.0  # Fallback to standard 30 FPS

        total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            total_frames = None

        duration = (total_frames / fps) if total_frames and fps > 0 else None

        self._metadata = MediaMetadata(
            media_type="video",
            width=w,
            height=h,
            fps=fps,
            total_frames=total_frames,
            duration=duration,
            loop_count=1,  # Videos play once by default unless player loops
        )
        self._current_pos: int = -1

    @property
    def metadata(self) -> MediaMetadata:
        return self._metadata

    def get_frame_at_time(self, target_time: float) -> Optional[DecodedFrame]:
        """Return the DecodedFrame at the specified timestamp in seconds."""
        fps = self._metadata.fps or 30.0
        total = self._metadata.total_frames
        frame_idx = max(0, int(target_time * fps))
        if total:
            frame_idx = min(frame_idx, total - 1)
        return self.get_frame_by_index(frame_idx)

    def get_frame_by_index(self, frame_index: int) -> Optional[DecodedFrame]:
        """Fetch a specific frame index efficiently."""
        # Fast path: if next sequential frame, read directly without slow seek
        if frame_index == self._current_pos + 1:
            ret, bgr_frame = self._cap.read()
        elif self._current_pos >= 0 and frame_index > self._current_pos and frame_index <= self._current_pos + 5:
            # Short forward skip: grab intermediate frames without full decode
            for _ in range(frame_index - self._current_pos - 1):
                self._cap.grab()
            ret, bgr_frame = self._cap.read()
        else:
            # Non-sequential jump or backward seek
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, bgr_frame = self._cap.read()

        if not ret or bgr_frame is None:
            return None

        self._current_pos = frame_index
        rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_frame, mode="RGB")
        fps = self._metadata.fps or 30.0
        frame_duration = 1.0 / fps
        return DecodedFrame(
            image=pil_image,
            frame_index=frame_index,
            timestamp=frame_index * frame_duration,
            duration=frame_duration,
        )

    def __iter__(self) -> Iterator[DecodedFrame]:
        """
        Stream frames sequentially until end of file.
        """
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        fps = self._metadata.fps or 30.0
        frame_duration = 1.0 / fps

        frame_idx = 0
        while self._cap.isOpened():
            ret, bgr_frame = self._cap.read()
            if not ret or bgr_frame is None:
                break

            # Convert OpenCV BGR to standard RGB PIL Image
            rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame, mode="RGB")

            timestamp = frame_idx * frame_duration

            yield DecodedFrame(
                image=pil_image,
                frame_index=frame_idx,
                timestamp=timestamp,
                duration=frame_duration,
            )
            frame_idx += 1

    def close(self) -> None:
        if hasattr(self, "_cap") and self._cap:
            try:
                self._cap.release()
            except Exception:
                pass


class StaticImageDecoder(BaseMediaDecoder):
    """
    Decoder wrapper for static images (PNG, JPEG, WebP, etc.).
    Yields exactly 1 frame, allowing uniform consumption in the visualizer pipeline.
    """

    def __init__(self, file_path: Union[str, Path]) -> None:
        self.path = Path(file_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"Image file not found: {self.path}")

        try:
            self._img = Image.open(self.path)
            self._img.load()
        except Exception as e:
            raise CorruptMediaError(f"Failed to open image file '{self.path}': {e}") from e

        self._metadata = MediaMetadata(
            media_type="static_image",
            width=self._img.width,
            height=self._img.height,
            fps=None,
            total_frames=1,
            duration=None,
            loop_count=1,
        )

    @property
    def metadata(self) -> MediaMetadata:
        return self._metadata

    def get_frame_at_time(self, target_time: float) -> Optional[DecodedFrame]:
        return self.get_frame_by_index(0)

    def get_frame_by_index(self, frame_index: int) -> Optional[DecodedFrame]:
        rgb_img = self._img.convert("RGB")
        return DecodedFrame(
            image=rgb_img,
            frame_index=0,
            timestamp=0.0,
            duration=0.1,
        )

    def __iter__(self) -> Iterator[DecodedFrame]:
        yield self.get_frame_by_index(0)

    def close(self) -> None:
        if hasattr(self, "_img") and self._img:
            try:
                self._img.close()
            except Exception:
                pass


def open_media(file_path: Union[str, Path]) -> BaseMediaDecoder:
    """
    Factory function to open and inspect any supported media file (GIF, Video, or Image).
    Returns an appropriate streaming BaseMediaDecoder instance.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file does not exist: {path}")

    ext = path.suffix.lower()

    if ext == ".gif":
        return GifDecoder(path)
    elif ext in VideoDecoder.SUPPORTED_EXTENSIONS:
        return VideoDecoder(path)
    elif ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return StaticImageDecoder(path)
    else:
        # Check header magic for GIF in case extension is missing or unusual
        try:
            with open(path, "rb") as f:
                head = f.read(6)
            if head.startswith((b"GIF87a", b"GIF89a")):
                return GifDecoder(path)
        except Exception:
            pass

        raise UnsupportedFormatError(f"Unsupported media file format for '{path.name}' (extension '{ext}')")
