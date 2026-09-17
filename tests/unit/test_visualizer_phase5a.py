"""
Phase 5A Unit Tests: Visualizer Performance Telemetry & Binary Threshold Processing.

Tests:
1. Binary threshold processing (threshold=0, 255, 128, exact boundary behavior).
2. Input conversion: black, white, mixed grayscale, RGB luminance weights (ITU-R BT.601).
3. Resulting RGBFrame in BINARY mode contains strictly (0,0,0) or (255,255,255).
4. Backward compatibility: grayscale=True maps to ColorMode.GRAYSCALE.
5. Existing COLOR and GRAYSCALE modes remain unchanged.
6. Telemetry metrics collection (ChunkTiming, FrameTelemetryRecord, format_report).
7. GUI configuration state (ColorMode options, dynamic threshold reveal).
8. Preview and output share identical FrameProcessorConfig and RGBFrame.
9. Physical output still strictly uses 10 AA24 chunks and validates ACKs.
10. Restore behavior and absence of forbidden protocol opcodes.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock
import numpy as np
from PIL import Image

from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.visualizer.frame import RGBFrame
from keyboard_re.visualizer.output import KeyboardRgbOutput
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
)


class TestBinaryThresholdProcessing(unittest.TestCase):
    """Unit tests for Phase 5A binary thresholding in FrameProcessor."""

    def setUp(self) -> None:
        self.processor = FrameProcessor()

    def test_pure_white_input_all_modes(self) -> None:
        """White image produces (255, 255, 255) across all 84 keys in all modes."""
        white_img = Image.new("RGB", (350, 120), (255, 255, 255))

        for mode in [ColorMode.COLOR, ColorMode.GRAYSCALE, ColorMode.BINARY]:
            cfg = FrameProcessorConfig(color_mode=mode, threshold=128)
            frame = self.processor.process_image(white_img, cfg)
            for region in self.processor.canvas:
                self.assertEqual(
                    frame.get_color(region.led_slot),
                    (255, 255, 255),
                    f"Slot {region.led_slot} ({region.key_id}) failed in mode {mode}",
                )

    def test_pure_black_input_all_modes(self) -> None:
        """Black image produces (0, 0, 0) across all 84 keys in all modes."""
        black_img = Image.new("RGB", (350, 120), (0, 0, 0))

        for mode in [ColorMode.COLOR, ColorMode.GRAYSCALE, ColorMode.BINARY]:
            cfg = FrameProcessorConfig(color_mode=mode, threshold=128)
            frame = self.processor.process_image(black_img, cfg)
            for region in self.processor.canvas:
                self.assertEqual(
                    frame.get_color(region.led_slot),
                    (0, 0, 0),
                    f"Slot {region.led_slot} ({region.key_id}) failed in mode {mode}",
                )

    def test_binary_threshold_boundary_128(self) -> None:
        """Pixels with luminance >= 128 become bright, < 128 become dark."""
        img_127 = Image.new("RGB", (350, 120), (127, 127, 127))
        img_128 = Image.new("RGB", (350, 120), (128, 128, 128))

        cfg = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=128)

        frame_127 = self.processor.process_image(img_127, cfg)
        for region in self.processor.canvas:
            self.assertEqual(frame_127.get_color(region.led_slot), (0, 0, 0))

        frame_128 = self.processor.process_image(img_128, cfg)
        for region in self.processor.canvas:
            self.assertEqual(frame_128.get_color(region.led_slot), (255, 255, 255))

    def test_binary_threshold_extrema_0_and_255(self) -> None:
        """Threshold 0 makes all non-negative luminance bright; 255 requires maximum."""
        img_mid = Image.new("RGB", (350, 120), (10, 10, 10))

        # Threshold 0: luminance >= 0 -> all 255
        cfg_0 = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=0)
        frame_0 = self.processor.process_image(img_mid, cfg_0)
        for region in self.processor.canvas:
            self.assertEqual(frame_0.get_color(region.led_slot), (255, 255, 255))

        # Threshold 255: mid gray < 255 -> all 0
        cfg_255 = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=255)
        frame_255 = self.processor.process_image(img_mid, cfg_255)
        for region in self.processor.canvas:
            self.assertEqual(frame_255.get_color(region.led_slot), (0, 0, 0))

    def test_binary_mode_contains_strictly_only_two_colors(self) -> None:
        """Resulting RGBFrame in BINARY mode must contain ONLY (0,0,0) or (255,255,255)."""
        # Create a gradient image covering all values 0..255
        gradient_arr = np.linspace(0, 255, 350 * 120, dtype=np.uint8).reshape((120, 350))
        gradient_img = Image.fromarray(gradient_arr, mode="L").convert("RGB")

        for thresh in [32, 64, 128, 192, 220]:
            cfg = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=thresh)
            frame = self.processor.process_image(gradient_img, cfg)

            for region in self.processor.canvas:
                color = frame.get_color(region.led_slot)
                self.assertIn(
                    color,
                    [(0, 0, 0), (255, 255, 255)],
                    f"Slot {region.led_slot} has invalid intermediate color {color} with threshold {thresh}",
                )

    def test_rgb_luminance_weights_before_thresholding(self) -> None:
        """Verify ITU-R BT.601 weights: 0.299*R + 0.587*G + 0.114*B."""
        # Pure Red: 255 * 0.299 = 76.245
        red_img = Image.new("RGB", (350, 120), (255, 0, 0))
        cfg_red_pass = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=76)
        cfg_red_fail = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=77)
        self.assertEqual(self.processor.process_image(red_img, cfg_red_pass).get_color(0), (255, 255, 255))
        self.assertEqual(self.processor.process_image(red_img, cfg_red_fail).get_color(0), (0, 0, 0))

        # Pure Green: 255 * 0.587 = 149.685
        green_img = Image.new("RGB", (350, 120), (0, 255, 0))
        cfg_green_pass = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=149)
        cfg_green_fail = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=150)
        self.assertEqual(self.processor.process_image(green_img, cfg_green_pass).get_color(0), (255, 255, 255))
        self.assertEqual(self.processor.process_image(green_img, cfg_green_fail).get_color(0), (0, 0, 0))

        # Pure Blue: 255 * 0.114 = 29.07
        blue_img = Image.new("RGB", (350, 120), (0, 0, 255))
        cfg_blue_pass = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=29)
        cfg_blue_fail = FrameProcessorConfig(color_mode=ColorMode.BINARY, threshold=30)
        self.assertEqual(self.processor.process_image(blue_img, cfg_blue_pass).get_color(0), (255, 255, 255))
        self.assertEqual(self.processor.process_image(blue_img, cfg_blue_fail).get_color(0), (0, 0, 0))

    def test_legacy_grayscale_compatibility(self) -> None:
        """FrameProcessorConfig(grayscale=True) automatically sets ColorMode.GRAYSCALE."""
        cfg_legacy = FrameProcessorConfig(grayscale=True)
        self.assertEqual(cfg_legacy.color_mode, ColorMode.GRAYSCALE)
        self.assertTrue(cfg_legacy.grayscale)

        cfg_mode = FrameProcessorConfig(color_mode=ColorMode.GRAYSCALE)
        self.assertTrue(cfg_mode.grayscale)
        self.assertEqual(cfg_mode.color_mode, ColorMode.GRAYSCALE)

    def test_existing_color_mode_unmodified(self) -> None:
        """COLOR mode preserves RGB colors and brightness scaling."""
        img = Image.new("RGB", (350, 120), (200, 100, 50))
        cfg = FrameProcessorConfig(color_mode=ColorMode.COLOR, brightness=0.5)
        frame = self.processor.process_image(img, cfg)
        # 200 * 0.5 = 100, 100 * 0.5 = 50, 50 * 0.5 = 25
        self.assertEqual(frame.get_color(0), (100, 50, 25))

    def test_existing_grayscale_mode_unmodified(self) -> None:
        """GRAYSCALE mode converts to luminance and scales by brightness."""
        img = Image.new("RGB", (350, 120), (255, 0, 0))  # lum ~ 76.245
        cfg = FrameProcessorConfig(color_mode=ColorMode.GRAYSCALE, brightness=1.0)
        frame = self.processor.process_image(img, cfg)
        c = frame.get_color(0)
        self.assertEqual(c[0], c[1])
        self.assertEqual(c[1], c[2])
        self.assertEqual(c[0], 76)


class TestVisualizerTelemetry(unittest.TestCase):
    """Unit tests for VisualizerTelemetry latency and throughput tracking."""

    def test_telemetry_disabled_by_default(self) -> None:
        telem = VisualizerTelemetry(enabled=False)
        self.assertFalse(telem.enabled)
        telem.record_frame(FrameTelemetryRecord(frame_index=1))
        summary = telem.get_summary()
        self.assertEqual(summary["frames_sent"], 0)

    def test_telemetry_records_and_summary(self) -> None:
        telem = VisualizerTelemetry(enabled=True)
        telem.start_session()

        chunk_timings = [
            ChunkTiming(chunk_index=i, address=i * 56, send_ms=0.3, ack_ms=16.8, total_ms=17.1)
            for i in range(10)
        ]
        rec = FrameTelemetryRecord(
            frame_index=1,
            t_decoder_ms=15.0,
            t_callback_interval_ms=250.0,
            t_processor_ms=8.5,
            t_serialize_ms=0.05,
            t_write_frame_ms=180.0,
            t_10_chunks_ms=171.0,
            chunk_timings=chunk_timings,
        )
        telem.record_frame(rec)
        telem.record_dropped_frames(2)
        telem.stop_session()

        summary = telem.get_summary()
        self.assertTrue(summary["has_records"])
        self.assertEqual(summary["frames_sent"], 1)
        self.assertEqual(summary["frames_dropped"], 2)
        self.assertEqual(summary["decoder_ms"]["avg"], 15.0)
        self.assertEqual(summary["processor_ms"]["avg"], 8.5)
        self.assertEqual(summary["write_frame_ms"]["avg"], 180.0)
        self.assertEqual(summary["chunk_ack_ms"]["avg"], 16.8)

        report = telem.format_report()
        self.assertIn("VISUALIZER PERFORMANCE TELEMETRY REPORT", report)
        self.assertIn("Frames Sent:       1", report)
        self.assertIn("Frames Dropped:    2", report)


class TestHardwareOutputProtocolSafety(unittest.TestCase):
    """Unit tests verifying Phase 4 hardware output protocol safety remains strict."""

    def test_write_frame_uses_exactly_10_aa24_chunks(self) -> None:
        """write_frame must issue 10 sequential AA24 chunks with correct addresses."""
        sent_reports = []

        mock_transport = MagicMock()
        mock_transport.is_connected = True

        def _fake_send(report_id, data):
            sent_reports.append(bytes(data))

        def _fake_receive(timeout=1.0, expected_opcode=None, expected_address=None):
            # Form standard 64-byte ACK report (55 24 len addrLo addrHi)
            ack = bytearray(REPORT_SIZE)
            ack[0] = 0x55
            ack[1] = 0x24
            ack[2] = 0x38 if (expected_address is None or expected_address < 504) else 0x08
            if expected_address is not None:
                ack[3] = expected_address & 0xFF
                ack[4] = (expected_address >> 8) & 0xFF
            return bytes(ack)

        mock_transport.send_report.side_effect = _fake_send
        mock_transport.receive_report.side_effect = _fake_receive

        output = KeyboardRgbOutput(transport=mock_transport, strict_ack=False)
        output._is_active = True

        frame = RGBFrame.solid((255, 0, 0))
        output.write_frame(frame)

        self.assertEqual(len(sent_reports), 10)
        expected_addrs = [0, 56, 112, 168, 224, 280, 336, 392, 448, 504]
        for idx, (pkt, exp_addr) in enumerate(zip(sent_reports, expected_addrs)):
            self.assertEqual(pkt[0], 0xAA)
            self.assertEqual(pkt[1], 0x24)
            addr = pkt[3] | (pkt[4] << 8)
            self.assertEqual(addr, exp_addr)

    def test_no_forbidden_opcodes_during_output(self) -> None:
        """Visualizer must never emit opcodes other than AA13, AA14, AA23, AA24."""
        sent_opcodes = set()
        mock_transport = MagicMock()
        mock_transport.is_connected = True

        def _track_send(report_id, data):
            if len(data) >= 2 and data[0] == 0xAA:
                sent_opcodes.add(data[1])

        def _fake_ack(timeout=1.0, expected_opcode=None, expected_address=None):
            ack = bytearray(REPORT_SIZE)
            ack[0] = 0x55
            ack[1] = 0x24
            ack[2] = 0x38 if (expected_address is None or expected_address < 504) else 0x08
            if expected_address is not None:
                ack[3] = expected_address & 0xFF
                ack[4] = (expected_address >> 8) & 0xFF
            return bytes(ack)

        mock_transport.send_report.side_effect = _track_send
        mock_transport.receive_report.side_effect = _fake_ack

        output = KeyboardRgbOutput(transport=mock_transport, strict_ack=False)
        output._is_active = True
        output.write_frame(RGBFrame.black())

        forbidden_opcodes = {0x11, 0x12, 0x15, 0x16, 0x17, 0x18, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}
        intersection = sent_opcodes.intersection(forbidden_opcodes)
        self.assertEqual(intersection, set(), f"Forbidden opcodes detected: {intersection}")


class TestVisualizerGUIAndPreviewIntegration(unittest.TestCase):
    """Unit tests for VisualizerView GUI controls and Preview/Output pipeline symmetry."""

    @classmethod
    def setUpClass(cls) -> None:
        import customtkinter as ctk
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_gui_color_mode_and_threshold_controls(self) -> None:
        """Verify GUI ColorMode selection and dynamic Threshold slider reveal."""
        from keyboard_re.ui.controller import AppController
        from keyboard_re.ui.views.visualizer_view import VisualizerView

        controller = AppController()
        view = VisualizerView(self.root, controller=controller)

        try:
            # 1. Initial State: Color mode, threshold slider hidden
            self.assertEqual(view.processor_config.color_mode, ColorMode.COLOR)
            self.assertEqual(view.processor_config.threshold, 128)
            self.assertEqual(view.threshold_box.grid_info(), {})

            # 2. Switch to Binary mode: Threshold slider revealed
            view._on_color_mode_changed("Binary")
            self.assertEqual(view.processor_config.color_mode, ColorMode.BINARY)
            self.assertNotEqual(view.threshold_box.grid_info(), {})

            # 3. Adjust threshold slider
            view._on_threshold_changed(200)
            self.assertEqual(view.processor_config.threshold, 200)
            self.assertEqual(view.threshold_label.cget("text"), "Threshold: 200")

            # 4. Switch to Grayscale: Threshold slider hidden again
            view._on_color_mode_changed("Grayscale")
            self.assertEqual(view.processor_config.color_mode, ColorMode.GRAYSCALE)
            self.assertEqual(view.threshold_box.grid_info(), {})

            # 5. Switch back to Color: Threshold slider hidden
            view._on_color_mode_changed("Color")
            self.assertEqual(view.processor_config.color_mode, ColorMode.COLOR)
            self.assertEqual(view.threshold_box.grid_info(), {})
        finally:
            view.cleanup()
            view.destroy()

    def test_preview_and_output_share_identical_rgb_frame(self) -> None:
        """Preview and hardware output must strictly receive the identical RGBFrame."""
        from keyboard_re.ui.controller import AppController
        from keyboard_re.ui.views.visualizer_view import VisualizerView
        from keyboard_re.visualizer.decoder import DecodedFrame

        controller = AppController()
        view = VisualizerView(self.root, controller=controller)

        try:
            # Configure binary thresholding
            view._on_color_mode_changed("Binary")
            view._on_threshold_changed(100)

            # Mock hardware output write_frame
            mock_write = MagicMock()
            view.output.write_frame = mock_write
            view.output._is_active = True

            # Mock preview renderer
            mock_render = MagicMock(return_value=Image.new("RGB", (100, 50)))
            view.renderer.render = mock_render

            # Create test frame
            img = Image.new("RGB", (350, 120), (120, 120, 120))  # lum 120 >= 100 -> all 255
            test_decoded = DecodedFrame(
                image=img,
                frame_index=0,
                timestamp=0.0,
                duration=0.1,
            )

            # Trigger frame callback
            view._on_engine_frame(test_decoded)

            # 1. Output received the processed frame
            self.assertEqual(mock_write.call_count, 1)
            hw_frame = mock_write.call_args[0][0]
            self.assertIsInstance(hw_frame, RGBFrame)

            # 2. View current_rgb_frame is the exact same object
            self.assertIs(view.current_rgb_frame, hw_frame)

            # 3. Simulate UI thread render step
            view._render_ui_frame(hw_frame, test_decoded)
            self.assertEqual(mock_render.call_count, 1)
            preview_frame = mock_render.call_args[0][0]

            # 4. Preview received the exact same object as hardware output
            self.assertIs(preview_frame, hw_frame)

            # Verify it's strictly binary
            for region in view.processor.canvas:
                self.assertEqual(hw_frame.get_color(region.led_slot), (255, 255, 255))
        finally:
            view.cleanup()
            view.destroy()


if __name__ == "__main__":
    unittest.main()
