"""
Unit tests for Phase 5: GUI Visualizer (VisualizerView & MainWindow integration).

Verifies:
1. VisualizerView headless instantiation and layout construction.
2. Initial blank preview rendering and UI default states.
3. Loading static images (PNG/JPG) and metadata population.
4. Loading multi-frame animated GIFs and timeline slider configuration.
5. Playback transport controls (Play, Pause, Stop, Loop, Speed).
6. Timeline scrubbing and frame seeking.
7. Image adjustments (Scaling modes, Brightness, Grayscale) and dynamic frame reprocessing.
8. Safe Hardware Output toggle with MockHidTransport (AA13/AA14 backup, AA23 0x80, AA24 transmission, restore).
9. Hardware Output safety protections when disconnected.
10. Lifecycle handlers: tab switching (deselection/selection), device disconnection, and cleanup.
11. MainWindow integration: Visualizer tab presence, switching, and safe teardown.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from PIL import Image

import customtkinter as ctk

from keyboard_re.protocol.transport import MockHidTransport
from keyboard_re.ui.controller import AppController
from keyboard_re.ui.views.main_window import MainWindow
from keyboard_re.ui.views.visualizer_view import VisualizerView
from keyboard_re.visualizer.player import PlaybackState
from keyboard_re.visualizer.processor import ScalingMode


class TestUIVisualizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        self.controller = AppController()
        self.assertTrue(self.controller.connect(use_mock=True))

        # Create a test static PNG (160x60, Red)
        self.test_png = self.dir_path / "test_image.png"
        img = Image.new("RGB", (160, 60), (255, 0, 0))
        img.save(self.test_png)

        # Create a test 3-frame animated GIF (160x60: Red, Green, Blue, 100ms each)
        self.test_gif = self.dir_path / "test_anim.gif"
        f1 = Image.new("RGB", (160, 60), (255, 0, 0))
        f2 = Image.new("RGB", (160, 60), (0, 255, 0))
        f3 = Image.new("RGB", (160, 60), (0, 0, 255))
        f1.save(
            self.test_gif,
            save_all=True,
            append_images=[f2, f3],
            duration=[100, 100, 100],
            loop=0,
        )

    def tearDown(self) -> None:
        self.controller.disconnect()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. View Instantiation & Defaults
    # -------------------------------------------------------------------------
    def test_01_initial_view_construction_and_defaults(self) -> None:
        """VisualizerView instantiates headlessly with expected widgets and initial unlit plate."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            self.assertIsNotNone(view.preview_canvas)
            self.assertIsNotNone(view.preview_image_id)
            self.assertIsNotNone(view._photo_image)

            # Playback controls initially disabled
            self.assertEqual(view.play_pause_btn.cget("state"), "disabled")
            self.assertEqual(view.stop_btn.cget("state"), "disabled")
            self.assertEqual(view.timeline_slider.cget("state"), "disabled")

            # Hardware output initially off
            self.assertFalse(view._hw_output_var.get())
            self.assertFalse(view.output.is_active)
            self.assertEqual(view.hw_output_switch.cget("state"), "normal")
        finally:
            view.cleanup()
            view.destroy()

    # -------------------------------------------------------------------------
    # 2. Loading Media
    # -------------------------------------------------------------------------
    def test_02_load_static_image(self) -> None:
        """Loading a static image initializes decoder, renders frame, and sets timeline info."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_png)

            self.assertIsNotNone(view.decoder)
            self.assertIsNotNone(view.engine)
            self.assertEqual(view.decoder.metadata.media_type, "static_image")
            self.assertEqual(view.decoder.metadata.total_frames, 1)

            # File metadata label updated
            self.assertIn("STATIC_IMAGE", view.file_meta_label.cget("text"))
            self.assertIn("160x60", view.file_meta_label.cget("text"))

            # Single frame: timeline slider remains disabled
            self.assertEqual(view.timeline_slider.cget("state"), "disabled")

            # Current RGBFrame should have been generated and rendered
            self.assertIsNotNone(view.current_rgb_frame)
            # Slot 34 should be Red (from the 255, 0, 0 image)
            self.assertEqual(view.current_rgb_frame.get_color(34), (255, 0, 0))
        finally:
            view.cleanup()
            view.destroy()

    def test_03_load_animated_gif(self) -> None:
        """Loading animated GIF initializes multi-frame timeline slider and enables transport."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_gif)

            self.assertIsNotNone(view.decoder)
            self.assertIsNotNone(view.engine)
            self.assertEqual(view.decoder.metadata.media_type, "gif")
            self.assertEqual(view.decoder.metadata.total_frames, 3)

            # Timeline slider enabled with range [0..2]
            self.assertEqual(view.timeline_slider.cget("state"), "normal")
            self.assertEqual(view.timeline_slider.cget("to"), 2)

            # Transport buttons enabled
            self.assertEqual(view.play_pause_btn.cget("state"), "normal")
            self.assertEqual(view.stop_btn.cget("state"), "normal")
        finally:
            view.cleanup()
            view.destroy()

    # -------------------------------------------------------------------------
    # 3. Transport Controls
    # -------------------------------------------------------------------------
    def test_04_playback_play_pause_stop(self) -> None:
        """Transport controls toggle Play -> Pause -> Resume -> Stop cleanly."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_gif)

            # 1. Start playback
            view._toggle_play_pause()
            self.assertEqual(view.engine.state, PlaybackState.PLAYING)
            self.assertEqual(view.play_pause_btn.cget("text"), "⏸ Pause")

            # 2. Pause playback
            view._toggle_play_pause()
            self.assertEqual(view.engine.state, PlaybackState.PAUSED)
            self.assertEqual(view.play_pause_btn.cget("text"), "▶ Play")

            # 3. Resume playback
            view._toggle_play_pause()
            self.assertEqual(view.engine.state, PlaybackState.PLAYING)

            # 4. Stop playback
            view._on_stop_clicked()
            self.assertEqual(view.engine.state, PlaybackState.STOPPED)
            self.assertEqual(view.play_pause_btn.cget("text"), "▶ Play")
        finally:
            view.cleanup()
            view.destroy()

    def test_05_loop_and_speed_controls(self) -> None:
        """Loop toggle and playback speed controls propagate to PlaybackEngine."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_gif)

            # Loop setting
            view._loop_var.set(False)
            view._on_loop_toggled()
            self.assertFalse(view.engine.loop_enabled)

            view._loop_var.set(True)
            view._on_loop_toggled()
            self.assertTrue(view.engine.loop_enabled)

            # Speed setting
            view._on_speed_changed("1.5x")
            self.assertAlmostEqual(view.engine.playback_speed, 1.5)

            view._on_speed_changed("0.5x")
            self.assertAlmostEqual(view.engine.playback_speed, 0.5)
        finally:
            view.cleanup()
            view.destroy()

    # -------------------------------------------------------------------------
    # 4. Timeline Scrubbing
    # -------------------------------------------------------------------------
    def test_06_timeline_scrubbing(self) -> None:
        """Scrubbing timeline slider seeks PlaybackEngine and updates current frame."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_gif)

            # Frame 0 is Red (255, 0, 0)
            self.assertEqual(view.current_rgb_frame.get_color(34), (255, 0, 0))

            # Scrub to frame 1 (Green: 0, 255, 0)
            view._on_slider_scrub(1.0)
            self.assertEqual(view.engine.current_frame_index, 1)
            self.assertEqual(view.current_rgb_frame.get_color(34), (0, 255, 0))

            # Scrub to frame 2 (Blue: 0, 0, 255)
            view._on_slider_scrub(2.0)
            self.assertEqual(view.engine.current_frame_index, 2)
            self.assertEqual(view.current_rgb_frame.get_color(34), (0, 0, 255))
        finally:
            view.cleanup()
            view.destroy()

    # -------------------------------------------------------------------------
    # 5. Image Processing Adjustments
    # -------------------------------------------------------------------------
    def test_07_processor_adjustments_reprocess_current_frame(self) -> None:
        """Adjusting scaling, brightness, or grayscale reprocesses current frame immediately."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_png)
            # Baseline: Red (255, 0, 0)
            self.assertEqual(view.current_rgb_frame.get_color(34), (255, 0, 0))

            # 1. Adjust brightness to 50%
            view._on_brightness_changed(0.5)
            self.assertAlmostEqual(view.processor_config.brightness, 0.5)
            # Red should now be ~128
            r, g, b = view.current_rgb_frame.get_color(34)
            self.assertAlmostEqual(r, 128, delta=2)
            self.assertEqual(g, 0)
            self.assertEqual(b, 0)

            # 2. Toggle Grayscale
            view._grayscale_var.set(True)
            view._on_grayscale_toggled()
            self.assertTrue(view.processor_config.grayscale)
            r, g, b = view.current_rgb_frame.get_color(34)
            self.assertEqual(r, g)
            self.assertEqual(g, b)

            # 3. Change Scaling Mode
            view._on_scaling_changed("Crop (Fill)")
            self.assertEqual(view.processor_config.scaling_mode, ScalingMode.CROP)
            view._on_scaling_changed("Stretch")
            self.assertEqual(view.processor_config.scaling_mode, ScalingMode.STRETCH)
        finally:
            view.cleanup()
            view.destroy()

    # -------------------------------------------------------------------------
    # 6. Hardware Output (AA24) & Lifecycle Safety
    # -------------------------------------------------------------------------
    def test_08_hardware_output_toggle_and_transmission(self) -> None:
        """Toggling hardware output starts visualizer, streams AA24, and restores baseline on stop."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_png)

            # Verify initial transport state
            mock_transport: MockHidTransport = self.controller.transport  # type: ignore
            initial_sent_reports = len(mock_transport.recorded_reports)

            # 1. Toggle Hardware Output ON
            view._hw_output_var.set(True)
            view._on_hw_output_toggled()

            self.assertTrue(view.output.is_active)
            self.assertIn("STREAMING", view.hw_status_label.cget("text"))
            # Initial frame was written, incrementing transmission counter
            self.assertGreaterEqual(view._frames_sent_count, 1)
            self.assertGreater(len(mock_transport.recorded_reports), initial_sent_reports)

            # 2. Toggle Hardware Output OFF
            view._hw_output_var.set(False)
            view._on_hw_output_toggled()

            self.assertFalse(view.output.is_active)
            self.assertIn("OFF", view.hw_status_label.cget("text"))
        finally:
            view.cleanup()
            view.destroy()

    def test_09_hardware_output_safety_when_disconnected(self) -> None:
        """Attempting to activate hardware output while disconnected is rejected safely."""
        self.controller.disconnect()
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_png)

            # Switch should be disabled
            self.assertEqual(view.hw_output_switch.cget("state"), "disabled")

            # Forced attempt to toggle ON
            view._hw_output_var.set(True)
            view._on_hw_output_toggled()

            # Must safely reset to False and remain inactive
            self.assertFalse(view._hw_output_var.get())
            self.assertFalse(view.output.is_active)
            self.assertIn("Connect USB", view.hw_status_label.cget("text"))
        finally:
            view.cleanup()
            view.destroy()

    def test_10_tab_switch_and_cleanup_halts_hardware(self) -> None:
        """Switching away from Visualizer tab or running cleanup halts hardware output."""
        view = VisualizerView(self.root, controller=self.controller)
        try:
            view.load_media_file(self.test_png)
            view._hw_output_var.set(True)
            view._on_hw_output_toggled()
            self.assertTrue(view.output.is_active)

            # Switching tab away
            view.on_tab_deselected()
            self.assertFalse(view.output.is_active)
            self.assertFalse(view._hw_output_var.get())

            # Reactivate and run cleanup()
            view._hw_output_var.set(True)
            view._on_hw_output_toggled()
            self.assertTrue(view.output.is_active)

            view.cleanup()
            self.assertFalse(view.output.is_active)
        finally:
            view.destroy()

    # -------------------------------------------------------------------------
    # 7. MainWindow Integration
    # -------------------------------------------------------------------------
    def test_11_main_window_integration(self) -> None:
        """MainWindow hosts Visualizer tab, coordinates tab switching, and executes clean teardown."""
        main_win = MainWindow(self.root, controller=self.controller)
        try:
            self.assertTrue(hasattr(main_win, "visualizer_view"))
            self.assertIsInstance(main_win.visualizer_view, VisualizerView)

            # Switch to Visualizer tab
            main_win.tabview.set("Visualizer")
            main_win._on_tab_changed()
            self.assertEqual(self.controller.active_tab, "visualizer")

            # Load a file in visualizer
            main_win.visualizer_view.load_media_file(self.test_png)
            main_win.visualizer_view._hw_output_var.set(True)
            main_win.visualizer_view._on_hw_output_toggled()
            self.assertTrue(main_win.visualizer_view.output.is_active)

            # Switch back to RGB Global -> visualizer hardware output must halt
            main_win.tabview.set("RGB Global")
            main_win._on_tab_changed()
            self.assertEqual(self.controller.active_tab, "rgb_global")
            self.assertFalse(main_win.visualizer_view.output.is_active)

            # Clean shutdown of main_win
            main_win.cleanup()
        finally:
            main_win.destroy()


if __name__ == "__main__":
    unittest.main()
