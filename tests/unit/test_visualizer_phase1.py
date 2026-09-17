"""
Unit tests for Keyboard Visualizer Phase 1.

Tests cover:
- Canonical canvas geometry and 84-key mapping from TYPE84_LAYOUT.
- Gap detection and normalized coordinate mapping.
- RGBFrame model, 512B buffer serialization, and diff analysis.
- Image to 84 RGB matrix conversion.
- Scaling modes (fit, crop, stretch).
- Area-averaged resampling.
- Brightness adjustment and grayscale conversion.
- Visual preview generation.
- Subsystem isolation (zero mutation of non-RGB subsystems).
"""

from __future__ import annotations

import unittest
import numpy as np
from PIL import Image

from keyboard_re.protocol.rgb import (
    LED_BUFFER_SIZE,
    parse_led_buffer,
)
from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT, TYPE84_LAYOUT
from keyboard_re.visualizer import (
    CANVAS_ASPECT_RATIO,
    CANVAS_HEIGHT_U,
    CANVAS_WIDTH_U,
    TOTAL_PHYSICAL_KEYS,
    CanvasPreviewRenderer,
    FrameProcessor,
    FrameProcessorConfig,
    KeyboardCanvas,
    RGBFrame,
    ScalingMode,
    slot_to_chunk_index,
)


class TestVisualizerPhase1(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas = KeyboardCanvas()
        self.processor = FrameProcessor(self.canvas)

    # -------------------------------------------------------------------------
    # 1. Canonical Canvas Geometry Tests
    # -------------------------------------------------------------------------
    def test_canonical_canvas_geometry(self) -> None:
        """Verify the canvas matches the physical 84-key Type 84 dimensions and source of truth."""
        self.assertEqual(len(self.canvas), 84)
        self.assertEqual(self.canvas.total_keys, TOTAL_PHYSICAL_KEYS)
        self.assertAlmostEqual(self.canvas.width_u, 17.5)
        self.assertAlmostEqual(self.canvas.height_u, 6.0)
        self.assertAlmostEqual(self.canvas.aspect_ratio, 17.5 / 6.0)

        # Check lookup by led_slot and key_id for every key
        for key in TYPE84_LAYOUT:
            region_by_slot = self.canvas.get_by_led_slot(key.led_slot)
            self.assertIsNotNone(region_by_slot)
            self.assertEqual(region_by_slot.key_id, key.key_id)

            region_by_id = self.canvas.get_by_key_id(key.key_id)
            self.assertIsNotNone(region_by_id)
            self.assertEqual(region_by_id.led_slot, key.led_slot)

            # Bounds validation
            self.assertGreater(region_by_slot.width, 0.0)
            self.assertGreater(region_by_slot.height, 0.0)
            nx1, ny1, nx2, ny2 = region_by_slot.normalized_bounds
            self.assertGreaterEqual(nx1, 0.0)
            self.assertGreaterEqual(ny1, 0.0)
            self.assertLessEqual(nx2, 1.0)
            self.assertLessEqual(ny2, 1.0)
            self.assertLess(nx1, nx2)
            self.assertLess(ny1, ny2)

    def test_canvas_gap_detection(self) -> None:
        """Verify gap detection properly distinguishes keys from layout voids."""
        # ESC is at (0.0, 0.0) with w=1, h=1 -> point (0.5, 0.5) is inside ESC
        esc_region = self.canvas.get_key_at_point(0.5, 0.5)
        self.assertIsNotNone(esc_region)
        self.assertEqual(esc_region.key_id, "ESC")
        self.assertFalse(self.canvas.is_gap(0.5, 0.5))

        # Points outside the keyboard plate are gaps
        self.assertTrue(self.canvas.is_gap(-1.0, 2.0))
        self.assertTrue(self.canvas.is_gap(20.0, 2.0))
        self.assertTrue(self.canvas.is_gap(5.0, 10.0))

        # Space above arrow keys: In row 3, Enter ends at x=15.0. Keys right of Enter (15.0..17.5) are empty.
        self.assertTrue(self.canvas.is_gap(16.0, 3.5))

    # -------------------------------------------------------------------------
    # 2. RGBFrame & 512B Buffer Serialization Tests
    # -------------------------------------------------------------------------
    def test_rgb_frame_initialization_and_serialization(self) -> None:
        """Verify RGBFrame creates 512B buffers with correct LED_IDs and colors."""
        frame = RGBFrame.solid((255, 128, 64))
        self.assertEqual(len(frame), 84)

        # All 84 physical keys must have the assigned color
        for slot in KEY_BY_LED_SLOT:
            self.assertEqual(frame.get_color(slot), (255, 128, 64))

        # Serialize to 512B wire buffer
        buf = frame.to_led_buffer()
        self.assertEqual(len(buf), LED_BUFFER_SIZE)

        # Validate wire buffer structure: slot i = [R, G, B, i]
        parsed = parse_led_buffer(buf)
        for slot in KEY_BY_LED_SLOT:
            self.assertEqual(parsed[slot], (255, 128, 64))

        # Non-physical slots should be (0, 0, 0)
        for slot in range(128):
            if slot not in KEY_BY_LED_SLOT:
                self.assertEqual(parsed[slot], (0, 0, 0))

        # Roundtrip from buffer
        reconstructed = RGBFrame.from_led_buffer(buf)
        for slot in KEY_BY_LED_SLOT:
            self.assertEqual(reconstructed.get_color(slot), (255, 128, 64))

    def test_differential_frame_detection(self) -> None:
        """Verify differential slot and chunk detection for transmission optimization."""
        frame_a = RGBFrame.solid((100, 100, 100))
        frame_b = frame_a.clone()

        # Identical frames -> 0 changed slots, 0 changed chunks
        self.assertEqual(frame_a.diff_slots(frame_b), set())
        self.assertEqual(frame_a.diff_chunks(frame_b), set())

        # Modify single key: Key 'W' = LED slot 35
        frame_b.set_color(35, (255, 0, 0))
        self.assertEqual(frame_a.diff_slots(frame_b), {35})
        # Slot 35 is in chunk index 35 // 14 = 2
        self.assertEqual(frame_a.diff_chunks(frame_b), {2})
        self.assertEqual(slot_to_chunk_index(35), 2)

        # Modify slot 0 ('ESC', chunk 0) and slot 84 ('LEFT', chunk 6)
        frame_b.set_color(0, (0, 255, 0))
        frame_b.set_color(84, (0, 0, 255))
        self.assertEqual(frame_a.diff_slots(frame_b), {0, 35, 84})
        self.assertEqual(frame_a.diff_chunks(frame_b), {0, 2, 6})

    # -------------------------------------------------------------------------
    # 3. Image → 84 RGB Values & Solid Color Tests
    # -------------------------------------------------------------------------
    def test_image_to_rgb_frame_solid_color(self) -> None:
        """Verify uniform image matching canvas ratio maps cleanly to all 84 physical keys."""
        # Canvas aspect ratio is 17.5:6 (e.g. 350x120 or 175x60)
        img = Image.new("RGB", (350, 120), (42, 84, 168))
        frame = self.processor.process_image(img)

        self.assertEqual(len(frame), 84)
        for slot in KEY_BY_LED_SLOT:
            r, g, b = frame.get_color(slot)
            self.assertEqual((r, g, b), (42, 84, 168))

    # -------------------------------------------------------------------------
    # 4. Aspect Ratio Scaling Modes (Fit, Crop, Stretch)
    # -------------------------------------------------------------------------
    def test_scaling_mode_stretch(self) -> None:
        """STRETCH scales an image to fill the canvas completely."""
        # Top half white (255, 255, 255), bottom half black (0, 0, 0)
        img = Image.new("RGB", (100, 100), (0, 0, 0))
        top_half = Image.new("RGB", (100, 50), (255, 255, 255))
        img.paste(top_half, (0, 0))

        cfg = FrameProcessorConfig(scaling_mode=ScalingMode.STRETCH)
        frame = self.processor.process_image(img, cfg)

        # Top row keys (row 0: ESC, F1..F12, PRTSC, HOME, END) should all be white
        for col_idx in [0, 1, 2, 3, 4, 13, 14, 15]:
            reg = self.canvas.regions()[col_idx]
            self.assertEqual(frame.get_color(reg.led_slot), (255, 255, 255))

        # Bottom row keys (row 5: LCTRL, SPACE, ARROWS) should all be black
        space_reg = self.canvas.get_by_key_id("SPACE")
        self.assertEqual(frame.get_color(space_reg.led_slot), (0, 0, 0))

    def test_scaling_mode_fit_letterbox(self) -> None:
        """FIT mode preserves aspect ratio, letterboxes with background color."""
        # Square white image 100x100
        square_white = Image.new("RGB", (100, 100), (255, 255, 255))

        # Canvas aspect ratio is 17.5 : 6.0 (~2.92). A square image inside FIT
        # must be centered horizontally with black padding on the left and right sides.
        cfg = FrameProcessorConfig(
            scaling_mode=ScalingMode.FIT,
            background_color=(0, 0, 0),
        )
        frame = self.processor.process_image(square_white, cfg)

        # Far left key (ESC at x=0..1) must fall into the letterbox black area
        esc_reg = self.canvas.get_by_key_id("ESC")
        self.assertEqual(frame.get_color(esc_reg.led_slot), (0, 0, 0))

        # Far right key (END at x=16.5..17.5) must fall into the letterbox black area
        end_reg = self.canvas.get_by_key_id("END")
        self.assertEqual(frame.get_color(end_reg.led_slot), (0, 0, 0))

        # Center keys (e.g. F6/F7 around x=7..9) must be white
        f6_reg = self.canvas.get_by_key_id("F6")
        self.assertEqual(frame.get_color(f6_reg.led_slot), (255, 255, 255))

    def test_scaling_mode_crop(self) -> None:
        """CROP mode covers the entire canvas, cropping excess image content."""
        # Square white image with black left and right 25% margins
        # [Black 25px | White 50px | Black 25px]
        img = Image.new("RGB", (100, 100), (0, 0, 0))
        white_stripe = Image.new("RGB", (50, 100), (255, 255, 255))
        img.paste(white_stripe, (25, 0))

        # In CROP mode onto 17.5:6 (wide), the image scales up by width, so the center
        # white band is stretched and the top/bottom is cropped.
        cfg = FrameProcessorConfig(scaling_mode=ScalingMode.CROP)
        frame = self.processor.process_image(img, cfg)

        # Center key 'G' should be white
        g_reg = self.canvas.get_by_key_id("G")
        self.assertEqual(frame.get_color(g_reg.led_slot), (255, 255, 255))

    # -------------------------------------------------------------------------
    # 5. Area-Averaged Resampling
    # -------------------------------------------------------------------------
    def test_area_sampling_averaging(self) -> None:
        """Verify that large keys average pixels across their surface area."""
        # Create an alternating black and white vertical stripe image (100x100)
        arr = np.zeros((120, 350, 3), dtype=np.uint8)
        arr[:, ::2] = 255  # every even column white, odd column black -> 50% average
        img = Image.fromarray(arr)

        cfg = FrameProcessorConfig(scaling_mode=ScalingMode.STRETCH)
        frame = self.processor.process_image(img, cfg)

        # Spacebar is 6.25u wide (spans dozens of alternating pixels)
        space_color = frame.get_color(self.canvas.get_by_key_id("SPACE").led_slot)
        # Average of 0 and 255 is ~127
        self.assertAlmostEqual(space_color[0], 127, delta=6)
        self.assertAlmostEqual(space_color[1], 127, delta=6)
        self.assertAlmostEqual(space_color[2], 127, delta=6)

    # -------------------------------------------------------------------------
    # 6. Brightness Multiplier & Grayscale Conversion
    # -------------------------------------------------------------------------
    def test_brightness_multiplier(self) -> None:
        """Verify brightness scaling from 0.0 to 1.0."""
        white_img = Image.new("RGB", (350, 120), (200, 200, 200))

        # Full brightness (1.0)
        cfg_full = FrameProcessorConfig(brightness=1.0)
        frame_full = self.processor.process_image(white_img, cfg_full)
        esc_color = frame_full.get_color(self.canvas.get_by_key_id("ESC").led_slot)
        self.assertEqual(esc_color, (200, 200, 200))

        # Half brightness (0.5)
        cfg_half = FrameProcessorConfig(brightness=0.5)
        frame_half = self.processor.process_image(white_img, cfg_half)
        esc_half = frame_half.get_color(self.canvas.get_by_key_id("ESC").led_slot)
        self.assertEqual(esc_half, (100, 100, 100))

        # Zero brightness (0.0) -> all black
        cfg_zero = FrameProcessorConfig(brightness=0.0)
        frame_zero = self.processor.process_image(white_img, cfg_zero)
        for slot in KEY_BY_LED_SLOT:
            self.assertEqual(frame_zero.get_color(slot), (0, 0, 0))

    def test_grayscale_mode(self) -> None:
        """Verify accurate ITU-R BT.601 luminance conversion (0.299 R + 0.587 G + 0.114 B)."""
        # Pure Red: 0.299 * 255 = 76.245 -> 76
        red_img = Image.new("RGB", (350, 120), (255, 0, 0))
        cfg_gray = FrameProcessorConfig(grayscale=True)
        frame_red = self.processor.process_image(red_img, cfg_gray)
        c_red = frame_red.get_color(self.canvas.get_by_key_id("ESC").led_slot)
        self.assertEqual(c_red, (76, 76, 76))

        # Pure Green: 0.587 * 255 = 149.685 -> 149
        green_img = Image.new("RGB", (350, 120), (0, 255, 0))
        frame_green = self.processor.process_image(green_img, cfg_gray)
        c_green = frame_green.get_color(self.canvas.get_by_key_id("ESC").led_slot)
        self.assertEqual(c_green, (149, 149, 149))

        # Pure Blue: 0.114 * 255 = 29.07 -> 29
        blue_img = Image.new("RGB", (350, 120), (0, 0, 255))
        frame_blue = self.processor.process_image(blue_img, cfg_gray)
        c_blue = frame_blue.get_color(self.canvas.get_by_key_id("ESC").led_slot)
        self.assertEqual(c_blue, (29, 29, 29))

    # -------------------------------------------------------------------------
    # 7. Visual Preview Generator Tests
    # -------------------------------------------------------------------------
    def test_preview_renderer(self) -> None:
        """Verify preview renderer outputs a valid PIL Image matching geometry."""
        renderer = CanvasPreviewRenderer(self.canvas, u_size=30.0)
        frame = RGBFrame.solid((255, 100, 50))
        preview_img = renderer.render(frame, show_labels=True)

        self.assertIsInstance(preview_img, Image.Image)
        self.assertEqual(preview_img.mode, "RGB")
        self.assertGreater(preview_img.width, 400)
        self.assertGreater(preview_img.height, 150)

    # -------------------------------------------------------------------------
    # 8. Subsystem Safety & Isolation Tests
    # -------------------------------------------------------------------------
    def test_subsystem_isolation(self) -> None:
        """Ensure visualizer only operates on Per-Key RGB and leaves other subsystems untouched."""
        import ast
        from pathlib import Path

        visualizer_dir = Path(__file__).resolve().parents[2] / "src" / "keyboard_re" / "visualizer"
        forbidden_subsystems = ["hall", "remap", "dks", "macro", "game_mode"]

        for py_file in visualizer_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for term in forbidden_subsystems:
                            self.assertNotIn(
                                term,
                                alias.name.lower(),
                                f"{py_file.name} must not import {alias.name}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for term in forbidden_subsystems:
                        self.assertNotIn(
                            term,
                            mod.lower(),
                            f"{py_file.name} must not import from {mod}",
                        )


if __name__ == "__main__":
    unittest.main()
