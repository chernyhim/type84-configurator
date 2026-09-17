"""
Keyboard Visualizer Package for IO by Red Square Type 84 Magnetic Black.

Subsystem for rendering images, animated GIFs, and video streams onto the keyboard's
84 physical RGB LEDs via the verified Per-Key RGB (AA24) protocol.
"""

from keyboard_re.visualizer.canvas import (
    CANVAS_ASPECT_RATIO,
    CANVAS_HEIGHT_U,
    CANVAS_WIDTH_U,
    TOTAL_PHYSICAL_KEYS,
    KeyboardCanvas,
    KeyCanvasRegion,
)
from keyboard_re.visualizer.decoder import (
    BaseMediaDecoder,
    CorruptMediaError,
    DecodedFrame,
    GifDecoder,
    MediaMetadata,
    StaticImageDecoder,
    UnsupportedFormatError,
    VideoDecoder,
    VisualizerDecoderError,
    open_media,
)
from keyboard_re.visualizer.frame import (
    RGBFrame,
    slot_to_chunk_index,
)
from keyboard_re.visualizer.player import (
    Clock,
    FakeClock,
    PlaybackEngine,
    PlaybackState,
    SystemClock,
)
from keyboard_re.visualizer.output import (
    HardwareOutputError,
    KeyboardRgbOutput,
)
from keyboard_re.visualizer.preview import CanvasPreviewRenderer
from keyboard_re.visualizer.processor import (
    ColorMode,
    FrameProcessor,
    FrameProcessorConfig,
    ScalingMode,
)
from keyboard_re.visualizer.telemetry import (
    ChunkTiming,
    FrameTelemetryRecord,
    VisualizerTelemetry,
    get_global_telemetry,
)

__all__ = [
    "CANVAS_ASPECT_RATIO",
    "CANVAS_HEIGHT_U",
    "CANVAS_WIDTH_U",
    "TOTAL_PHYSICAL_KEYS",
    "BaseMediaDecoder",
    "CanvasPreviewRenderer",
    "ChunkTiming",
    "Clock",
    "ColorMode",
    "CorruptMediaError",
    "DecodedFrame",
    "FakeClock",
    "FrameProcessor",
    "FrameProcessorConfig",
    "FrameTelemetryRecord",
    "GifDecoder",
    "HardwareOutputError",
    "KeyCanvasRegion",
    "KeyboardCanvas",
    "KeyboardRgbOutput",
    "MediaMetadata",
    "PlaybackEngine",
    "PlaybackState",
    "RGBFrame",
    "ScalingMode",
    "StaticImageDecoder",
    "SystemClock",
    "UnsupportedFormatError",
    "VideoDecoder",
    "VisualizerDecoderError",
    "VisualizerTelemetry",
    "get_global_telemetry",
    "open_media",
    "slot_to_chunk_index",
]
