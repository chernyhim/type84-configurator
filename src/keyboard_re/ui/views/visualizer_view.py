"""
Visualizer View for IO by Red Square Type 84 Configurator.

Provides interactive LED matrix visualizer controls:
- Real-time 84-key virtual keyboard preview (CanvasPreviewRenderer).
- Media source loading (GIF, MP4/AVI/MKV, PNG/JPG/BMP/WebP).
- Playback controls: Play/Pause, Stop, Timeline scrubber, Loop, Speed.
- Image processing adjustments: Scaling (Fit/Crop/Stretch), Brightness, Grayscale.
- Hardware Output toggle (AA24) with safe start, live streaming, and auto-restoration.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from typing import Optional, Tuple
import customtkinter as ctk
from PIL import ImageTk

from keyboard_re.ui.controller import AppController
from keyboard_re.visualizer.decoder import (
    BaseMediaDecoder,
    DecodedFrame,
    open_media,
)
from keyboard_re.visualizer.frame import RGBFrame
from keyboard_re.visualizer.output import HardwareOutputError, KeyboardRgbOutput
from keyboard_re.visualizer.player import PlaybackEngine, PlaybackState
from keyboard_re.visualizer.preview import CanvasPreviewRenderer
from keyboard_re.visualizer.processor import (
    ColorMode,
    FrameProcessor,
    FrameProcessorConfig,
    ScalingMode,
)

logger = logging.getLogger(__name__)


class VisualizerView(ctk.CTkFrame):
    """
    GUI panel for loading, processing, previewing, and streaming
    images, GIFs, and videos onto the 84-key LED matrix.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AppController,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=("#f4f4f5", "#18181b"), **kwargs)
        self.controller = controller

        # Core visualizer backend components
        self.processor = FrameProcessor()
        self.processor_config = FrameProcessorConfig(
            scaling_mode=ScalingMode.FIT,
            brightness=1.0,
            color_mode=ColorMode.COLOR,
            threshold=128,
        )
        self.renderer = CanvasPreviewRenderer(
            u_size=32.0,
            margin=10.0,
            key_gap=2.5,
            corner_radius=3.5,
        )
        self.output = KeyboardRgbOutput()
        self.decoder: Optional[BaseMediaDecoder] = None
        self.engine: Optional[PlaybackEngine] = None

        # State tracking
        self.current_filepath: Optional[Path] = None
        self.current_rgb_frame: Optional[RGBFrame] = None
        self._last_decoded_frame: Optional[DecodedFrame] = None
        self._photo_image: Optional[ImageTk.PhotoImage] = None
        self._is_scrubbing: bool = False
        self._ui_render_pending: bool = False
        self._speed: float = 1.0
        self._frames_sent_count: int = 0

        # Tkinter variables
        self._loop_var = ctk.BooleanVar(value=True)
        self._grayscale_var = ctk.BooleanVar(value=False)
        self._color_mode_var = ctk.StringVar(value="Color")
        self._threshold_var = ctk.IntVar(value=128)
        self._hw_output_var = ctk.BooleanVar(value=False)

        # Build UI layout
        self._build_layout()
        self._render_blank_preview()
        self.update_display()

    # -------------------------------------------------------------------------
    # Layout Construction
    # -------------------------------------------------------------------------
    def _build_layout(self) -> None:
        """Create main two-column layout."""
        self.grid_columnconfigure(0, weight=3)  # Left: Preview & Playback
        self.grid_columnconfigure(1, weight=2)  # Right: Media, Settings, Output
        self.grid_rowconfigure(1, weight=1)

        # Header Title
        title_box = ctk.CTkFrame(self, fg_color="transparent")
        title_box.grid(row=0, column=0, columnspan=2, padx=15, pady=(12, 6), sticky="ew")

        title = ctk.CTkLabel(
            title_box,
            text="Type 84 LED Matrix Visualizer",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        title.pack(side="left")

        self.header_status = ctk.CTkLabel(
            title_box,
            text="Phase 5 Interactive GUI",
            font=ctk.CTkFont(size=12),
            text_color="#a1a1aa",
        )
        self.header_status.pack(side="right")

        # Left Column: Preview and Playback Transport
        left_column = ctk.CTkFrame(self, fg_color="transparent")
        left_column.grid(row=1, column=0, padx=(15, 8), pady=(0, 12), sticky="nsew")
        left_column.grid_columnconfigure(0, weight=1)

        self._build_preview_panel(left_column)
        self._build_transport_panel(left_column)

        # Right Column: Media Source, Processing, Hardware Output
        right_column = ctk.CTkFrame(self, fg_color="transparent")
        right_column.grid(row=1, column=1, padx=(8, 15), pady=(0, 12), sticky="nsew")
        right_column.grid_columnconfigure(0, weight=1)

        self._build_media_card(right_column)
        self._build_processor_card(right_column)
        self._build_hardware_card(right_column)

    def _build_preview_panel(self, parent: ctk.CTkFrame) -> None:
        """Construct the live 84-key canvas preview panel."""
        preview_frame = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#202024"), corner_radius=8)
        preview_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        preview_frame.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(
            preview_frame,
            text="Real-Time 84-Key Plate Preview",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        # Canvas widget sizing matched to CanvasPreviewRenderer
        w = self.renderer.width_px
        h = self.renderer.height_px
        self.preview_canvas = tk.Canvas(
            preview_frame,
            width=w,
            height=h,
            bg="#121216",
            highlightthickness=1,
            highlightbackground="#27272a",
        )
        self.preview_canvas.grid(row=1, column=0, padx=12, pady=(0, 10))
        self.preview_image_id = self.preview_canvas.create_image(w // 2, h // 2)

    def _build_transport_panel(self, parent: ctk.CTkFrame) -> None:
        """Construct timeline scrubber and playback control buttons."""
        ctrl_frame = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#202024"), corner_radius=8)
        ctrl_frame.grid(row=1, column=0, sticky="ew")
        ctrl_frame.grid_columnconfigure(0, weight=1)

        # Timeline Scrubber Bar
        timeline_box = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        timeline_box.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="ew")
        timeline_box.grid_columnconfigure(0, weight=1)

        self.timeline_slider = ctk.CTkSlider(
            timeline_box,
            from_=0,
            to=1,
            number_of_steps=1,
            command=self._on_slider_scrub,
        )
        self.timeline_slider.set(0)
        self.timeline_slider.configure(state="disabled")
        self.timeline_slider.grid(row=0, column=0, sticky="ew")
        self.timeline_slider.bind("<ButtonPress-1>", self._on_slider_press)
        self.timeline_slider.bind("<ButtonRelease-1>", self._on_slider_release)

        self.time_label = ctk.CTkLabel(
            timeline_box,
            text="00:00.0 / 00:00.0 (Frame 0/0)",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
        )
        self.time_label.grid(row=1, column=0, pady=(2, 0), sticky="e")

        # Transport Buttons Bar
        btn_bar = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        btn_bar.grid(row=1, column=0, padx=12, pady=(4, 12), sticky="ew")

        self.play_pause_btn = ctk.CTkButton(
            btn_bar,
            text="▶ Play",
            width=90,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._toggle_play_pause,
            state="disabled",
        )
        self.play_pause_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_bar,
            text="⏹ Stop",
            width=80,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_stop_clicked,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 14))

        self.loop_switch = ctk.CTkSwitch(
            btn_bar,
            text="Loop",
            variable=self._loop_var,
            command=self._on_loop_toggled,
            onvalue=True,
            offvalue=False,
            width=46,
        )
        self.loop_switch.pack(side="left", padx=(0, 16))

        speed_lbl = ctk.CTkLabel(btn_bar, text="Speed:", font=ctk.CTkFont(size=11))
        speed_lbl.pack(side="left", padx=(0, 4))

        self.speed_menu = ctk.CTkOptionMenu(
            btn_bar,
            values=["0.25x", "0.5x", "1.0x", "1.5x", "2.0x"],
            width=80,
            command=self._on_speed_changed,
        )
        self.speed_menu.set("1.0x")
        self.speed_menu.pack(side="left")

    def _build_media_card(self, parent: ctk.CTkFrame) -> None:
        """Construct media file selector and metadata card."""
        card = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#202024"), corner_radius=8)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(
            card,
            text="Media Source",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.open_file_btn = ctk.CTkButton(
            card,
            text="Open Media File...",
            width=160,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._on_open_file_clicked,
        )
        self.open_file_btn.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")

        self.file_name_label = ctk.CTkLabel(
            card,
            text="No media loaded",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        )
        self.file_name_label.grid(row=2, column=0, padx=12, pady=(0, 2), sticky="w")

        self.file_meta_label = ctk.CTkLabel(
            card,
            text="Supports GIF, MP4, AVI, MKV, PNG, JPG, BMP, WebP",
            font=ctk.CTkFont(size=10),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.file_meta_label.grid(row=3, column=0, padx=12, pady=(0, 8), sticky="w")

    def _build_processor_card(self, parent: ctk.CTkFrame) -> None:
        """Construct image adjustment controls (Scaling, Brightness, Grayscale)."""
        card = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#202024"), corner_radius=8)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(
            card,
            text="Image Processing",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        # Scaling Mode
        scale_box = ctk.CTkFrame(card, fg_color="transparent")
        scale_box.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="ew")
        scale_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(scale_box, text="Scaling:", font=ctk.CTkFont(size=11), width=65, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.scale_menu = ctk.CTkOptionMenu(
            scale_box,
            values=["Fit (Aspect)", "Crop (Fill)", "Stretch"],
            command=self._on_scaling_changed,
            width=140,
        )
        self.scale_menu.set("Fit (Aspect)")
        self.scale_menu.grid(row=0, column=1, sticky="w")

        # Brightness Slider
        bright_box = ctk.CTkFrame(card, fg_color="transparent")
        bright_box.grid(row=2, column=0, padx=12, pady=(0, 6), sticky="ew")
        bright_box.grid_columnconfigure(1, weight=1)

        self.brightness_label = ctk.CTkLabel(
            bright_box,
            text="Brightness: 100%",
            font=ctk.CTkFont(size=11),
            width=110,
            anchor="w",
        )
        self.brightness_label.grid(row=0, column=0, sticky="w")

        self.brightness_slider = ctk.CTkSlider(
            bright_box,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            command=self._on_brightness_changed,
        )
        self.brightness_slider.set(1.0)
        self.brightness_slider.grid(row=0, column=1, sticky="ew")

        # Color Mode Selection (Color, Grayscale, Binary / Threshold)
        mode_box = ctk.CTkFrame(card, fg_color="transparent")
        mode_box.grid(row=3, column=0, padx=12, pady=(0, 6), sticky="ew")
        mode_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(mode_box, text="Color Mode:", font=ctk.CTkFont(size=11), width=75, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.color_mode_segmented = ctk.CTkSegmentedButton(
            mode_box,
            values=["Color", "Grayscale", "Binary"],
            command=self._on_color_mode_changed,
            variable=self._color_mode_var,
        )
        self.color_mode_segmented.grid(row=0, column=1, sticky="ew")

        # Binary Threshold Slider (revealed only in Binary mode)
        self.threshold_box = ctk.CTkFrame(card, fg_color="transparent")
        self.threshold_box.grid(row=4, column=0, padx=12, pady=(0, 8), sticky="ew")
        self.threshold_box.grid_columnconfigure(1, weight=1)

        self.threshold_label = ctk.CTkLabel(
            self.threshold_box,
            text="Threshold: 128",
            font=ctk.CTkFont(size=11),
            width=110,
            anchor="w",
        )
        self.threshold_label.grid(row=0, column=0, sticky="w")

        self.threshold_slider = ctk.CTkSlider(
            self.threshold_box,
            from_=0,
            to=255,
            number_of_steps=255,
            command=self._on_threshold_changed,
        )
        self.threshold_slider.set(128)
        self.threshold_slider.grid(row=0, column=1, sticky="ew")

        # Initially hidden until "Binary" mode is selected
        self.threshold_box.grid_remove()

    def _build_hardware_card(self, parent: ctk.CTkFrame) -> None:
        """Construct hardware output toggle (AA24) and status card."""
        card = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#202024"), corner_radius=8)
        card.grid(row=2, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(
            card,
            text="Hardware Output (AA24 USB)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="w")

        self.hw_output_switch = ctk.CTkSwitch(
            card,
            text="Send to Physical Keyboard",
            variable=self._hw_output_var,
            command=self._on_hw_output_toggled,
            onvalue=True,
            offvalue=False,
        )
        self.hw_output_switch.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")

        self.hw_status_label = ctk.CTkLabel(
            card,
            text="Status: Hardware Output OFF (Preview Only)",
            font=ctk.CTkFont(size=11),
            text_color="#a1a1aa",
            anchor="w",
        )
        self.hw_status_label.grid(row=2, column=0, padx=12, pady=(0, 2), sticky="w")

        self.hw_stats_label = ctk.CTkLabel(
            card,
            text="Frames Transmitted: 0",
            font=ctk.CTkFont(size=10),
            text_color="#71717a",
            anchor="w",
        )
        self.hw_stats_label.grid(row=3, column=0, padx=12, pady=(0, 8), sticky="w")

    # -------------------------------------------------------------------------
    # Media Loading
    # -------------------------------------------------------------------------
    def _on_open_file_clicked(self) -> None:
        """Display file picker dialog to load media."""
        filetypes = [
            ("Supported Media", "*.gif;*.mp4;*.avi;*.mkv;*.mov;*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
            ("Animated GIFs", "*.gif"),
            ("Videos", "*.mp4;*.avi;*.mkv;*.mov"),
            ("Images", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
            ("All Files", "*.*"),
        ]
        path_str = filedialog.askopenfilename(
            title="Open Image, GIF, or Video for Visualizer",
            filetypes=filetypes,
        )
        if path_str:
            self.load_media_file(Path(path_str))

    def load_media_file(self, path: Path | str) -> None:
        """Load and initialize media decoder and playback engine."""
        path = Path(path)
        if not path.is_file():
            logger.error("Media file does not exist: %s", path)
            return

        # Halt and close existing engine if present
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None

        try:
            self.decoder = open_media(path)
            self.current_filepath = path
        except Exception as exc:
            logger.error("Failed to decode media file '%s': %s", path, exc)
            self.file_name_label.configure(text=f"Error: {exc}")
            return

        meta = self.decoder.metadata
        self.file_name_label.configure(text=path.name)

        fps_str = f"{meta.fps:.1f} fps" if meta.fps else "var fps"
        frames_str = f"{meta.total_frames} frames" if meta.total_frames else "stream"
        dur_str = f"{meta.duration:.1f}s" if meta.duration else "unknown dur"
        self.file_meta_label.configure(
            text=f"{meta.media_type.upper()} | {meta.width}x{meta.height} | {frames_str} | {fps_str} | {dur_str}"
        )

        # Setup timeline slider
        total_frames = meta.total_frames or 1
        if total_frames > 1:
            self.timeline_slider.configure(
                from_=0,
                to=total_frames - 1,
                number_of_steps=total_frames - 1,
                state="normal",
            )
        else:
            self.timeline_slider.configure(from_=0, to=1, state="disabled")
        self.timeline_slider.set(0)

        # Construct PlaybackEngine
        self.engine = PlaybackEngine(
            decoder=self.decoder,
        )
        self.engine.set_loop(bool(self._loop_var.get()))
        self.engine.set_speed(self._speed)
        self.engine.on_frame = self._on_engine_frame
        self.engine.on_started = lambda: self.after(0, self._update_playback_ui_state)
        self.engine.on_paused = lambda: self.after(0, self._update_playback_ui_state)
        self.engine.on_stopped = lambda: self.after(0, self._update_playback_ui_state)
        self.engine.on_finished = lambda: self.after(0, self._on_engine_finished)
        self.engine.on_error = lambda exc: self.after(0, self._on_engine_error, exc)

        # Enable playback buttons
        self.play_pause_btn.configure(state="normal", text="▶ Play")
        self.stop_btn.configure(state="normal")

        # Decode & display initial frame 0 immediately
        first_frame = self.decoder.get_frame_by_index(0)
        if first_frame is not None:
            self._on_engine_frame(first_frame)

    # -------------------------------------------------------------------------
    # Playback Engine Callbacks & Thread Safety
    # -------------------------------------------------------------------------
    def _on_engine_frame(self, decoded_frame: DecodedFrame) -> None:
        """
        Called when PlaybackEngine emits a decoded frame (worker thread).
        Converts image to RGBFrame, transmits to hardware if active, and dispatches to UI.
        """
        self._last_decoded_frame = decoded_frame

        # Process frame to 84-key RGBFrame
        rgb_frame = self.processor.process_image(decoded_frame.image, self.processor_config)
        self.current_rgb_frame = rgb_frame

        # Write to physical keyboard if hardware output is active
        if self.output.is_active:
            try:
                self.output.write_frame(rgb_frame)
                self._frames_sent_count += 1
            except Exception as exc:
                logger.error("Hardware transmission error: %s", exc)
                self.after(0, self._on_hardware_error, exc)

        # Schedule preview render on Tkinter main thread
        if not self._ui_render_pending:
            self._ui_render_pending = True
            self.after(0, self._render_ui_frame, rgb_frame, decoded_frame)

    def _render_ui_frame(
        self,
        rgb_frame: RGBFrame,
        decoded_frame: Optional[DecodedFrame] = None,
    ) -> None:
        """Render RGBFrame onto the Tkinter canvas (main UI thread)."""
        self._ui_render_pending = False

        # Render simulated plate to PIL Image
        img = self.renderer.render(rgb_frame)
        self._photo_image = ImageTk.PhotoImage(img)
        self.preview_canvas.itemconfig(self.preview_image_id, image=self._photo_image)

        # Update timeline info
        if decoded_frame is not None and self.decoder is not None:
            if not self._is_scrubbing and (self.decoder.metadata.total_frames or 0) > 1:
                self.timeline_slider.set(decoded_frame.frame_index)

            cur_t = decoded_frame.timestamp
            tot_t = self.decoder.metadata.duration or 0.0
            cur_idx = decoded_frame.frame_index + 1
            tot_idx = self.decoder.metadata.total_frames or 1
            self.time_label.configure(
                text=f"{cur_t:04.1f}s / {tot_t:04.1f}s (Frame {cur_idx}/{tot_idx})"
            )

        if self.output.is_active:
            self.hw_stats_label.configure(text=f"Frames Transmitted: {self._frames_sent_count}")

    def _render_blank_preview(self) -> None:
        """Display an unlit black keyboard plate initially."""
        black_frame = RGBFrame.black()
        self._render_ui_frame(black_frame)

    def _on_engine_finished(self) -> None:
        """Called when non-looping media reaches its end."""
        self._update_playback_ui_state()

    def _on_engine_error(self, exc: Exception) -> None:
        """Handle playback worker thread errors."""
        logger.error("PlaybackEngine error: %s", exc)
        self._update_playback_ui_state()

    def _on_hardware_error(self, exc: Exception) -> None:
        """Handle hardware transmission error: deactivate switch and alert user."""
        self._hw_output_var.set(False)
        self.hw_status_label.configure(
            text=f"Hardware Error: {exc}",
            text_color="#ef4444",
        )
        self.update_display()

    # -------------------------------------------------------------------------
    # Transport Controls
    # -------------------------------------------------------------------------
    def _toggle_play_pause(self) -> None:
        """Toggle playback between Playing and Paused."""
        if not self.engine:
            return

        if self.engine.state == PlaybackState.PLAYING:
            self.engine.pause()
        else:
            self.engine.play()
        self._update_playback_ui_state()

    def _on_stop_clicked(self) -> None:
        """Stop playback and rewind to start."""
        if self.engine:
            self.engine.stop()
            # Rewind to frame 0
            if self.decoder:
                first = self.decoder.get_frame_by_index(0)
                if first is not None:
                    self._on_engine_frame(first)
        self._update_playback_ui_state()

    def _update_playback_ui_state(self) -> None:
        """Synchronize play/pause button state and text."""
        if not self.engine:
            self.play_pause_btn.configure(state="disabled", text="▶ Play", fg_color="#0284c7")
            self.stop_btn.configure(state="disabled")
            return

        if self.engine.state == PlaybackState.PLAYING:
            self.play_pause_btn.configure(text="⏸ Pause", fg_color="#f59e0b", hover_color="#d97706")
        else:
            self.play_pause_btn.configure(text="▶ Play", fg_color="#0284c7", hover_color="#0369a1")

    def _on_loop_toggled(self) -> None:
        """Update loop setting on active PlaybackEngine."""
        loop_val = bool(self._loop_var.get())
        if self.engine:
            self.engine.set_loop(loop_val)

    def _on_speed_changed(self, speed_str: str) -> None:
        """Update playback speed multiplier."""
        speed_map = {
            "0.25x": 0.25,
            "0.5x": 0.5,
            "1.0x": 1.0,
            "1.5x": 1.5,
            "2.0x": 2.0,
        }
        self._speed = speed_map.get(speed_str, 1.0)
        if self.engine:
            self.engine.set_speed(self._speed)

    # -------------------------------------------------------------------------
    # Timeline Scrubbing
    # -------------------------------------------------------------------------
    def _on_slider_press(self, event: tk.Event) -> None:
        self._is_scrubbing = True

    def _on_slider_release(self, event: tk.Event) -> None:
        self._is_scrubbing = False

    def _on_slider_scrub(self, val: float) -> None:
        """Seek playback engine when timeline slider is moved."""
        if not self.decoder or not self.engine:
            return

        idx = int(round(val))
        target_frame = self.decoder.get_frame_by_index(idx)
        if target_frame is not None:
            self.engine.seek(target_frame.timestamp)

    # -------------------------------------------------------------------------
    # Image Adjustments
    # -------------------------------------------------------------------------
    def _on_scaling_changed(self, choice: str) -> None:
        """Update scaling mode and reprocess current frame."""
        mode_map = {
            "Fit (Aspect)": ScalingMode.FIT,
            "Crop (Fill)": ScalingMode.CROP,
            "Stretch": ScalingMode.STRETCH,
        }
        mode = mode_map.get(choice, ScalingMode.FIT)
        self.processor_config = FrameProcessorConfig(
            scaling_mode=mode,
            brightness=self.processor_config.brightness,
            color_mode=self.processor_config.color_mode,
            threshold=self.processor_config.threshold,
        )
        self._reprocess_current_frame()

    def _on_brightness_changed(self, val: float) -> None:
        """Update brightness multiplier and reprocess current frame."""
        pct = int(round(val * 100))
        self.brightness_label.configure(text=f"Brightness: {pct}%")
        self.processor_config = FrameProcessorConfig(
            scaling_mode=self.processor_config.scaling_mode,
            brightness=val,
            color_mode=self.processor_config.color_mode,
            threshold=self.processor_config.threshold,
        )
        self._reprocess_current_frame()

    def _on_color_mode_changed(self, mode_str: str) -> None:
        """Handle color mode selection (Color, Grayscale, Binary)."""
        mode_map = {
            "Color": ColorMode.COLOR,
            "Grayscale": ColorMode.GRAYSCALE,
            "Binary": ColorMode.BINARY,
            "Binary / Threshold": ColorMode.BINARY,
        }
        mode = mode_map.get(mode_str, ColorMode.COLOR)

        if mode == ColorMode.BINARY:
            self.threshold_box.grid()
        else:
            self.threshold_box.grid_remove()

        self.processor_config = FrameProcessorConfig(
            scaling_mode=self.processor_config.scaling_mode,
            brightness=self.processor_config.brightness,
            color_mode=mode,
            threshold=self.processor_config.threshold,
        )
        self._reprocess_current_frame()

    def _on_threshold_changed(self, val: float) -> None:
        """Handle binary threshold slider adjustments."""
        t_int = int(round(val))
        self.threshold_label.configure(text=f"Threshold: {t_int}")
        self.processor_config = FrameProcessorConfig(
            scaling_mode=self.processor_config.scaling_mode,
            brightness=self.processor_config.brightness,
            color_mode=self.processor_config.color_mode,
            threshold=t_int,
        )
        self._reprocess_current_frame()

    def _on_grayscale_toggled(self) -> None:
        """Legacy helper for grayscale toggle."""
        is_gray = bool(self._grayscale_var.get())
        mode = ColorMode.GRAYSCALE if is_gray else ColorMode.COLOR
        self.processor_config = FrameProcessorConfig(
            scaling_mode=self.processor_config.scaling_mode,
            brightness=self.processor_config.brightness,
            color_mode=mode,
            threshold=self.processor_config.threshold,
        )
        self._reprocess_current_frame()

    def _reprocess_current_frame(self) -> None:
        """Reprocess and re-render last decoded frame when settings change."""
        if self._last_decoded_frame is not None:
            self._on_engine_frame(self._last_decoded_frame)

    # -------------------------------------------------------------------------
    # Hardware Output (AA24)
    # -------------------------------------------------------------------------
    def _on_hw_output_toggled(self) -> None:
        """Handle hardware output switch toggle."""
        enable = bool(self._hw_output_var.get())

        if enable:
            # Check transport connection
            if not self.controller.is_connected or self.controller.transport is None:
                self._hw_output_var.set(False)
                self.hw_status_label.configure(
                    text="Status: Connect USB keyboard first!",
                    text_color="#f59e0b",
                )
                return

            self.output.transport = self.controller.transport
            try:
                self.output.start_visualizer()
                self._frames_sent_count = 0
                self.hw_status_label.configure(
                    text="Status: STREAMING to Physical Keyboard (AA24)",
                    text_color="#34d399",
                )
                # If we have a current frame, send it immediately
                if self.current_rgb_frame is not None:
                    self.output.write_frame(self.current_rgb_frame)
                    self._frames_sent_count += 1
            except Exception as exc:
                self._hw_output_var.set(False)
                logger.error("Failed to start hardware output: %s", exc)
                self.hw_status_label.configure(
                    text=f"Status: Start Failed ({exc})",
                    text_color="#ef4444",
                )
        else:
            self._stop_hardware_output()

    def _stop_hardware_output(self) -> None:
        """Safely stop hardware streaming and restore keyboard baseline."""
        if self.output.is_active:
            try:
                self.output.stop_visualizer(restore=True)
            except Exception as exc:
                logger.warning("Error stopping hardware visualizer: %s", exc)

        self._hw_output_var.set(False)
        self.hw_status_label.configure(
            text="Status: Hardware Output OFF (Preview Only)",
            text_color="#a1a1aa",
        )

    # -------------------------------------------------------------------------
    # Lifecycle Coordination
    # -------------------------------------------------------------------------
    def update_display(self) -> None:
        """Called on controller connection/disconnection or state updates."""
        if self.controller.is_connected:
            self.hw_output_switch.configure(state="normal")
            if not self.output.is_active:
                self.hw_status_label.configure(
                    text="Status: Ready (Preview Only - toggle to Stream)",
                    text_color="#a1a1aa",
                )
        else:
            # Disconnected
            if self.output.is_active:
                self._stop_hardware_output()
            self.hw_output_switch.configure(state="disabled")
            self.hw_status_label.configure(
                text="Status: Keyboard Disconnected (Preview Only)",
                text_color="#71717a",
            )

    def on_tab_selected(self) -> None:
        """Called when user switches into the Visualizer tab."""
        self.update_display()

    def on_tab_deselected(self) -> None:
        """
        Called when user switches away from Visualizer tab.
        Halts hardware output and restores device baseline for safety.
        """
        if self.output.is_active:
            self._stop_hardware_output()

    def on_device_disconnected(self) -> None:
        """Called when user clicks Disconnect in main header."""
        if self.output.is_active:
            self._stop_hardware_output()
        self.update_display()

    def cleanup(self) -> None:
        """Clean shutdown when application window closes."""
        self._stop_hardware_output()
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None
