"""
FrameProcessor: Converts source images into physical keyboard RGB frames.

Supports:
- Three scaling modes: FIT (letterbox), CROP (center crop), and STRETCH.
- Area-averaged downsampling across physical key boundaries.
- Brightness adjustment (0.0 .. 1.0) with clipping.
- Grayscale conversion via standard ITU-R BT.601 luminance weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image

from keyboard_re.visualizer.canvas import KeyboardCanvas
from keyboard_re.visualizer.frame import RGBFrame


class ScalingMode(str, Enum):
    """Image to keyboard canvas aspect-ratio scaling modes."""
    FIT = "fit"          # Preserves aspect ratio, fits entire image inside canvas, letterboxes
    CROP = "crop"        # Preserves aspect ratio, scales to cover entire canvas, crops excess
    STRETCH = "stretch"  # Stretches image to fill canvas dimensions directly


class ColorMode(str, Enum):
    """Color conversion modes for image processing."""
    COLOR = "color"          # Full RGB color output
    GRAYSCALE = "grayscale"  # Standard ITU-R BT.601 luminance grayscale
    BINARY = "binary"        # High-contrast two-state thresholding (strictly 0 or 255)


@dataclass(frozen=True)
class FrameProcessorConfig:
    """Configuration parameters for image-to-matrix conversion."""
    scaling_mode: ScalingMode = ScalingMode.FIT
    brightness: float = 1.0                              # Output scaling multiplier (0.0 .. 1.0)
    grayscale: bool = False                              # Grayscale / luminance mode (legacy compat)
    color_mode: ColorMode = ColorMode.COLOR              # Color conversion mode
    threshold: int = 128                                 # Binary threshold cutoff (0 .. 255)
    background_color: Tuple[int, int, int] = (0, 0, 0)   # Letterbox background color
    grid_width: int = 350                                # Canvas raster resolution width (px)
    grid_height: int = 120                               # Canvas raster resolution height (px)

    def __post_init__(self) -> None:
        # Clamp brightness to 0.0 .. 1.0
        clamped_brightness = max(0.0, min(1.0, float(self.brightness)))
        if clamped_brightness != self.brightness:
            object.__setattr__(self, "brightness", clamped_brightness)

        # Clamp threshold to 0 .. 255
        clamped_thresh = max(0, min(255, int(self.threshold)))
        if clamped_thresh != self.threshold:
            object.__setattr__(self, "threshold", clamped_thresh)

        # Ensure backward compatibility between grayscale: bool and color_mode: ColorMode
        if self.grayscale and self.color_mode == ColorMode.COLOR:
            object.__setattr__(self, "color_mode", ColorMode.GRAYSCALE)
        elif self.color_mode == ColorMode.GRAYSCALE and not self.grayscale:
            object.__setattr__(self, "grayscale", True)


class FrameProcessor:
    """
    Transforms arbitrary source images into 84-key RGBFrames using the canonical
    keyboard canvas geometry and area-based resampling.
    """

    def __init__(self, canvas: Optional[KeyboardCanvas] = None) -> None:
        self.canvas = canvas or KeyboardCanvas()
        self._cached_boxes: dict[Tuple[int, int], list] = {}

    def process_image(
        self,
        image: Union[Image.Image, np.ndarray],
        config: Optional[FrameProcessorConfig] = None,
    ) -> RGBFrame:
        """
        Convert a PIL Image or numpy array to an RGBFrame for the keyboard.
        """
        cfg = config or FrameProcessorConfig()

        # 1. Standardize to PIL Image in RGB mode
        if isinstance(image, np.ndarray):
            # If OpenCV BGR format (3 channels), handle correctly if needed or assume RGB
            if image.ndim == 2:
                pil_img = Image.fromarray(image, mode="L").convert("RGB")
            elif image.ndim == 3 and image.shape[2] == 3:
                pil_img = Image.fromarray(image, mode="RGB")
            elif image.ndim == 3 and image.shape[2] == 4:
                pil_img = Image.fromarray(image, mode="RGBA").convert("RGB")
            else:
                raise ValueError(f"Unsupported numpy array shape: {image.shape}")
        elif isinstance(image, Image.Image):
            pil_img = image.convert("RGB")
        else:
            raise TypeError(f"Expected PIL Image or numpy array, got {type(image)}")

        # 2. Rescale onto canvas grid (W, H) according to scaling mode
        gw = cfg.grid_width
        gh = cfg.grid_height
        canvas_img = self._scale_to_canvas(pil_img, gw, gh, cfg)

        # Convert canvas raster to numpy array for fast area averaging (H, W, 3)
        raster_arr = np.array(canvas_img, dtype=np.float32)

        # 3. Retrieve or compute cached sampling boxes for physical keys
        boxes = self._cached_boxes.get((gw, gh))
        if boxes is None:
            boxes = []
            for region in self.canvas:
                px1, py1, px2, py2 = region.to_pixel_box(gw, gh)
                cx, cy = region.normalized_center
                cx_px = min(gw - 1, int(cx * gw))
                cy_px = min(gh - 1, int(cy * gh))
                boxes.append((region.led_slot, px1, py1, px2, py2, cx_px, cy_px))
            self._cached_boxes[(gw, gh)] = boxes

        # 4. Sample and transform each physical key's surface area
        frame = RGBFrame()
        brightness = cfg.brightness
        mode = cfg.color_mode
        thresh = cfg.threshold

        for led_slot, px1, py1, px2, py2, cx_px, cy_px in boxes:
            sub_region = raster_arr[py1:py2, px1:px2]

            if sub_region.size == 0:
                r, g, b = raster_arr[cy_px, cx_px][:3]
            else:
                mean_color = sub_region.mean(axis=(0, 1))
                r, g, b = mean_color[0], mean_color[1], mean_color[2]

            # Mode dispatch
            if mode == ColorMode.BINARY:
                # ITU-R BT.601 luminance conversion
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                # Strict two-state thresholding: bright (255, 255, 255) or dark (0, 0, 0)
                val = 255 if lum >= thresh else 0
                frame.set_color(led_slot, (val, val, val))

            elif mode == ColorMode.GRAYSCALE or cfg.grayscale:
                # ITU-R BT.601 standard grayscale + brightness scaling
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                gray_val = int(np.clip(lum * brightness, 0.0, 255.0))
                frame.set_color(led_slot, (gray_val, gray_val, gray_val))

            else:
                # Full color + brightness scaling
                r_val = int(np.clip(r * brightness, 0.0, 255.0))
                g_val = int(np.clip(g * brightness, 0.0, 255.0))
                b_val = int(np.clip(b * brightness, 0.0, 255.0))
                frame.set_color(led_slot, (r_val, g_val, b_val))

        return frame

    def _scale_to_canvas(
        self,
        img: Image.Image,
        target_w: int,
        target_h: int,
        cfg: FrameProcessorConfig,
    ) -> Image.Image:
        """
        Scale source image to (target_w, target_h) canvas using configured ScalingMode.
        """
        src_w, src_h = img.size
        mode = cfg.scaling_mode

        if mode == ScalingMode.STRETCH:
            return img.resize((target_w, target_h), Image.Resampling.BILINEAR)

        elif mode == ScalingMode.FIT:
            # Letterbox: preserve aspect ratio, fit within target_w x target_h
            scale = min(target_w / src_w, target_h / src_h)
            new_w = max(1, int(round(src_w * scale)))
            new_h = max(1, int(round(src_h * scale)))
            resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

            # Paste centered onto background
            canvas = Image.new("RGB", (target_w, target_h), cfg.background_color)
            paste_x = (target_w - new_w) // 2
            paste_y = (target_h - new_h) // 2
            canvas.paste(resized, (paste_x, paste_y))
            return canvas

        elif mode == ScalingMode.CROP:
            # Center crop: preserve aspect ratio, cover target_w x target_h
            scale = max(target_w / src_w, target_h / src_h)
            new_w = max(1, int(round(src_w * scale)))
            new_h = max(1, int(round(src_h * scale)))
            resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

            # Crop centered region
            crop_x = (new_w - target_w) // 2
            crop_y = (new_h - target_h) // 2
            return resized.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))

        else:
            raise ValueError(f"Unknown scaling mode: {mode}")
