"""
FPS Diagnostic Runner for Physical Keyboard Visualizer Pipeline.

Runs a real media playback session (e.g. Bad Apple) on the connected physical keyboard,
collecting and displaying precise per-stage latency telemetry:
- Decoder fetch time
- PlaybackEngine tick/callback intervals
- FrameProcessor processing time
- RGBFrame serialization (to_led_buffer)
- KeyboardRgbOutput.write_frame() total time
- Per-chunk send_report latency
- Per-chunk receive_report/ACK latency
- Full cycle per chunk (10 chunks total)
- Total time for all 10 chunks
- Sent frames vs dropped frames
- Output FPS
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time

from keyboard_re.transport.native_hid import NativeHidTransport
from keyboard_re.visualizer.decoder import open_media
from keyboard_re.visualizer.output import KeyboardRgbOutput
from keyboard_re.visualizer.player import PlaybackEngine
from keyboard_re.visualizer.processor import FrameProcessor, FrameProcessorConfig, ScalingMode
from keyboard_re.visualizer.telemetry import VisualizerTelemetry

sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("visualizer.diagnostics")


def run_fps_diagnostics(
    media_path: str,
    duration_sec: float = 10.0,
    grayscale: bool = False,
    color_mode: str = "color",
    threshold: int = 128,
) -> VisualizerTelemetry:
    """Run diagnostic session and return telemetry."""
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path}")

    from keyboard_re.visualizer.processor import ColorMode

    mode_val = ColorMode(color_mode) if color_mode in ("color", "grayscale", "binary") else ColorMode.COLOR
    if grayscale:
        mode_val = ColorMode.GRAYSCALE

    print("\n" + "=" * 60)
    print("PHYSICAL KEYBOARD FPS DIAGNOSTICS")
    print(f"Media file: {path.name}")
    print(f"Duration:   {duration_sec:.1f}s")
    print(f"Color Mode: {mode_val.value} (Threshold: {threshold})")
    print("=" * 60)

    # 1. Open HID Transport
    print("1. Connecting to physical keyboard...", flush=True)
    transport = NativeHidTransport()
    transport.open()
    print("   Connected successfully.", flush=True)

    # 2. Setup Telemetry
    telemetry = VisualizerTelemetry(enabled=True)

    # 3. Setup Hardware Output
    output = KeyboardRgbOutput(
        transport=transport,
        timeout=1.5,
        strict_ack=True,
        telemetry=telemetry,
    )

    # 4. Open Media Decoder
    print(f"2. Opening media decoder: {path.name}...", flush=True)
    decoder = open_media(path)
    meta = decoder.metadata
    print(f"   Resolution: {meta.width}x{meta.height}, FPS: {meta.fps}, Frames: {meta.total_frames}", flush=True)

    # Instrument decoder to measure fetch time
    orig_get_frame = decoder.get_frame_at_time
    last_fetch_ms = 0.0

    def _timed_get_frame(t: float):
        nonlocal last_fetch_ms
        t0 = time.perf_counter()
        f = orig_get_frame(t)
        t1 = time.perf_counter()
        last_fetch_ms = (t1 - t0) * 1000.0
        return f

    decoder.get_frame_at_time = _timed_get_frame

    # 5. Setup Processor & Engine
    processor = FrameProcessor()
    config = FrameProcessorConfig(
        scaling_mode=ScalingMode.FIT,
        brightness=1.0,
        color_mode=mode_val,
        threshold=threshold,
    )

    engine = PlaybackEngine(
        decoder=decoder,
        run_worker=True,
    )

    # Attach custom frame handler to pass decoder fetch time
    def _on_engine_frame(decoded_frame):
        if not output.is_active:
            return
        t_interval = telemetry.record_callback_interval()
        t_p0 = time.perf_counter()
        rgb_frame = processor.process_image(decoded_frame.image, config)
        t_p1 = time.perf_counter()
        t_proc_ms = (t_p1 - t_p0) * 1000.0

        output.write_frame(
            rgb_frame,
            t_processor_ms=t_proc_ms,
            t_callback_interval_ms=t_interval,
            t_decoder_ms=last_fetch_ms,
        )
        print(f"   [Frame #{decoded_frame.frame_index:03d}] Proc: {t_proc_ms:.1f}ms | Sent to USB", flush=True)

    engine.on_frame = _on_engine_frame
    engine.on_error = lambda e: print(f"[!] ENGINE ERROR: {e}", flush=True)

    try:
        print("3. Starting hardware output & preserving baseline states...", flush=True)
        output.start()
        print("   Baseline saved. Switching to EFFECT_CUSTOM (0x80).", flush=True)

        telemetry.start_session()
        print("4. Starting playback engine...", flush=True)
        engine.play()

        print(f"   Streaming for {duration_sec:.1f}s...", flush=True)
        t_end = time.time() + duration_sec
        while time.time() < t_end:
            time.sleep(0.5)
            # Update dropped frames from engine
            telemetry.record_dropped_frames(engine.dropped_frames - telemetry._frames_dropped)

        print("\n5. Stopping playback & restoring keyboard baseline...", flush=True)
        engine.stop()
        output.stop(restore=True)
        telemetry.stop_session()
        print("   Keyboard baseline successfully restored.", flush=True)

    except Exception as exc:
        print(f"\n[!] Diagnostic error: {exc}", flush=True)
        try:
            output.stop(restore=True)
        except Exception:
            pass
        raise
    finally:
        try:
            engine.close()
        except Exception:
            pass
        decoder.close()
        transport.close()

    print("\n" + telemetry.format_report(), flush=True)
    print("\nPlaybackEngine Internal Stats:", flush=True)
    print(f"  Emitted frames: {engine.emitted_frames}", flush=True)
    print(f"  Dropped frames: {engine.dropped_frames}", flush=True)
    print("=" * 60 + "\n", flush=True)

    return telemetry


def main() -> None:
    parser = argparse.ArgumentParser(description="FPS Diagnostic Runner for Keyboard Visualizer")
    parser.add_argument(
        "--file",
        type=str,
        default=r"c:\BetterThing\screenshots\bad_apple_task_manager.mp4",
        help="Path to media file",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Playback duration in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--grayscale",
        action="store_true",
        help="Enable grayscale processing (shortcut for --mode grayscale)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["color", "grayscale", "binary"],
        default="color",
        help="Color processing mode: color, grayscale, binary (default: color)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=128,
        help="Binary threshold cutoff (0..255, default: 128)",
    )
    args = parser.parse_args()

    run_fps_diagnostics(
        media_path=args.file,
        duration_sec=args.duration,
        grayscale=args.grayscale,
        color_mode=args.mode,
        threshold=args.threshold,
    )


if __name__ == "__main__":
    main()
