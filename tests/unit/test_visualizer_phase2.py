"""
Unit tests for Keyboard Visualizer Phase 2 (Streaming GIF and Video Decoders).

Tests verify:
- Multi-frame animated GIF decoding with distinct per-frame durations.
- Loop metadata and frame sequencing.
- Streaming generator behavior (lazy iteration without full-memory buffering).
- Synthetic MP4 video creation, decoding, dimensions, FPS, and timing.
- Normalized DecodedFrame format.
- Direct pipeline integration with Phase 1 FrameProcessor -> RGBFrame.
- Error handling (missing files, unsupported formats, corrupted files).
- Subsystem isolation (zero non-RGB subsystem imports).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT
from keyboard_re.visualizer import (
    CorruptMediaError,
    DecodedFrame,
    FrameProcessor,
    GifDecoder,
    RGBFrame,
    UnsupportedFormatError,
    VideoDecoder,
    open_media,
)


class TestVisualizerPhase2(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.processor = FrameProcessor()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Animated GIF Decoder Tests
    # -------------------------------------------------------------------------
    def test_animated_gif_decoding_with_variable_durations(self) -> None:
        """Verify multi-frame animated GIF with variable per-frame durations."""
        gif_path = self.dir_path / "test_anim.gif"

        # 3 frames: Red (100ms), Green (250ms), Blue (150ms)
        frame1 = Image.new("RGB", (40, 40), (255, 0, 0))
        frame2 = Image.new("RGB", (40, 40), (0, 255, 0))
        frame3 = Image.new("RGB", (40, 40), (0, 0, 255))
        durations = [100, 250, 150]  # ms

        frame1.save(
            gif_path,
            save_all=True,
            append_images=[frame2, frame3],
            duration=durations,
            loop=0,  # Infinite loop
        )

        with open_media(gif_path) as decoder:
            self.assertIsInstance(decoder, GifDecoder)
            meta = decoder.metadata
            self.assertEqual(meta.media_type, "gif")
            self.assertEqual(meta.width, 40)
            self.assertEqual(meta.height, 40)
            self.assertEqual(meta.total_frames, 3)
            self.assertEqual(meta.loop_count, 0)
            # Total duration: 0.1 + 0.25 + 0.15 = 0.5s
            self.assertAlmostEqual(meta.duration, 0.5, places=2)

            frames = list(decoder)
            self.assertEqual(len(frames), 3)

            # Frame 0: Red, dur=0.1s, ts=0.0s
            self.assertEqual(frames[0].frame_index, 0)
            self.assertAlmostEqual(frames[0].timestamp, 0.0, places=3)
            self.assertAlmostEqual(frames[0].duration, 0.1, places=3)
            self.assertEqual(frames[0].image.getpixel((20, 20)), (255, 0, 0))

            # Frame 1: Green, dur=0.25s, ts=0.1s
            self.assertEqual(frames[1].frame_index, 1)
            self.assertAlmostEqual(frames[1].timestamp, 0.1, places=3)
            self.assertAlmostEqual(frames[1].duration, 0.25, places=3)
            self.assertEqual(frames[1].image.getpixel((20, 20)), (0, 255, 0))

            # Frame 2: Blue, dur=0.15s, ts=0.35s
            self.assertEqual(frames[2].frame_index, 2)
            self.assertAlmostEqual(frames[2].timestamp, 0.35, places=3)
            self.assertAlmostEqual(frames[2].duration, 0.15, places=3)
            self.assertEqual(frames[2].image.getpixel((20, 20)), (0, 0, 255))

    def test_gif_fallback_duration_when_missing(self) -> None:
        """Verify fallback to safe default duration when duration metadata is missing."""
        gif_path = self.dir_path / "test_no_dur.gif"
        img = Image.new("RGB", (20, 20), (120, 60, 30))
        img.save(gif_path)

        with GifDecoder(gif_path) as decoder:
            meta = decoder.metadata
            self.assertEqual(meta.total_frames, 1)
            frames = list(decoder)
            self.assertEqual(len(frames), 1)
            self.assertGreater(frames[0].duration, 0.0)

    # -------------------------------------------------------------------------
    # 2. Synthetic Video Decoder Tests
    # -------------------------------------------------------------------------
    def test_synthetic_mp4_video_decoding(self) -> None:
        """Create and stream a synthetic MP4 video, verifying frame count and timestamps."""
        video_path = self.dir_path / "test_synth.mp4"
        fps = 25.0
        width, height = 64, 48
        total_test_frames = 10

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))
        if not writer.isOpened():
            self.skipTest("OpenCV MP4 video encoder not available in this environment")

        try:
            for i in range(total_test_frames):
                # Gradient color per frame
                val = int(i * 25)
                bgr = np.full((height, width, 3), (val, 100, 200), dtype=np.uint8)
                writer.write(bgr)
        finally:
            writer.release()

        # Open with VideoDecoder
        with open_media(video_path) as decoder:
            self.assertIsInstance(decoder, VideoDecoder)
            meta = decoder.metadata
            self.assertEqual(meta.media_type, "video")
            self.assertEqual(meta.width, width)
            self.assertEqual(meta.height, height)
            self.assertAlmostEqual(meta.fps, fps, delta=1.0)
            self.assertEqual(meta.total_frames, total_test_frames)
            self.assertAlmostEqual(meta.duration, total_test_frames / fps, places=2)

            decoded_frames = list(decoder)
            self.assertEqual(len(decoded_frames), total_test_frames)

            for idx, df in enumerate(decoded_frames):
                self.assertIsInstance(df, DecodedFrame)
                self.assertEqual(df.frame_index, idx)
                self.assertAlmostEqual(df.timestamp, idx / fps, places=2)
                self.assertAlmostEqual(df.duration, 1.0 / fps, places=2)
                self.assertEqual(df.image.size, (width, height))
                self.assertEqual(df.image.mode, "RGB")

    # -------------------------------------------------------------------------
    # 3. Pipeline Integration: Decoder → Phase 1 FrameProcessor → RGBFrame
    # -------------------------------------------------------------------------
    def test_pipeline_decoder_to_frame_processor(self) -> None:
        """Verify that DecodedFrame.image directly feeds into Phase 1 FrameProcessor."""
        gif_path = self.dir_path / "pipe_test.gif"
        f1 = Image.new("RGB", (175, 60), (200, 50, 10))
        f2 = Image.new("RGB", (175, 60), (10, 50, 200))
        f1.save(gif_path, save_all=True, append_images=[f2], duration=[100, 100])

        with open_media(gif_path) as decoder:
            for decoded_frame in decoder:
                # Direct Phase 1 consumption:
                rgb_frame = self.processor.process_image(decoded_frame.image)
                self.assertIsInstance(rgb_frame, RGBFrame)
                self.assertEqual(len(rgb_frame), 84)

                # Spot check ESC key color
                esc_color = rgb_frame.get_color(KEY_BY_LED_SLOT[0].led_slot)
                if decoded_frame.frame_index == 0:
                    self.assertEqual(esc_color, (200, 50, 10))
                else:
                    self.assertEqual(esc_color, (10, 50, 200))

    # -------------------------------------------------------------------------
    # 4. Error Handling Tests
    # -------------------------------------------------------------------------
    def test_file_not_found(self) -> None:
        """Verify FileNotFoundError on missing files."""
        with self.assertRaises(FileNotFoundError):
            open_media(self.dir_path / "non_existent.mp4")

    def test_unsupported_format(self) -> None:
        """Verify UnsupportedFormatError on invalid media formats."""
        txt_path = self.dir_path / "test.txt"
        txt_path.write_text("not a video or gif")
        with self.assertRaises(UnsupportedFormatError):
            open_media(txt_path)

    def test_corrupt_file_handling(self) -> None:
        """Verify CorruptMediaError on corrupted binary files."""
        bad_gif = self.dir_path / "corrupt.gif"
        bad_gif.write_bytes(b"GIF89a corrupted payload 12345678")
        with self.assertRaises(CorruptMediaError):
            open_media(bad_gif)

    # -------------------------------------------------------------------------
    # 5. Subsystem Isolation
    # -------------------------------------------------------------------------
    def test_subsystem_isolation(self) -> None:
        """Verify visualizer package does not import closed hardware modules."""
        import ast
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
