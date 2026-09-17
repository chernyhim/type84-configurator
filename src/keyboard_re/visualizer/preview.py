"""
CanvasPreviewRenderer: Generates visual 2D keyboard previews of RGBFrames.

Renders all 84 physical keys in their authentic physical positions, dimensions,
and gaps onto a dark keyboard plate.
"""

from __future__ import annotations

from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

from keyboard_re.visualizer.canvas import KeyboardCanvas
from keyboard_re.visualizer.frame import RGBFrame


class CanvasPreviewRenderer:
    """
    Renders an RGBFrame into a PIL Image simulating the physical Type 84 keyboard.
    """

    def __init__(
        self,
        canvas: Optional[KeyboardCanvas] = None,
        u_size: float = 36.0,
        margin: float = 10.0,
        key_gap: float = 3.0,
        corner_radius: float = 4.0,
        plate_color: Tuple[int, int, int] = (18, 18, 22),
        border_color: Tuple[int, int, int] = (40, 40, 48),
        show_labels: bool = True,
    ) -> None:
        self.canvas = canvas or KeyboardCanvas()
        self.u_size = float(u_size)
        self.margin = float(margin)
        self.key_gap = float(key_gap)
        self.corner_radius = float(corner_radius)
        self.plate_color = plate_color
        self.border_color = border_color
        self.show_labels = show_labels

        self.width_px = int(round(self.canvas.width_u * self.u_size + 2 * self.margin))
        self.height_px = int(round(self.canvas.height_u * self.u_size + 2 * self.margin))

    def render(
        self,
        frame: RGBFrame,
        show_labels: Optional[bool] = None,
    ) -> Image.Image:
        """
        Render an RGBFrame to a PIL Image.
        """
        img = Image.new("RGB", (self.width_px, self.height_px), self.plate_color)
        draw = ImageDraw.Draw(img)
        labels_enabled = self.show_labels if show_labels is None else show_labels

        # Draw outer casing border
        draw.rounded_rectangle(
            [2, 2, self.width_px - 3, self.height_px - 3],
            radius=6,
            outline=self.border_color,
            width=2,
        )

        half_gap = self.key_gap / 2.0
        rad = self.corner_radius

        for region in self.canvas:
            k = region.key
            # Key physical bounds in pixels
            x1 = self.margin + k.x * self.u_size + half_gap
            y1 = self.margin + k.y * self.u_size + half_gap
            x2 = self.margin + (k.x + k.width) * self.u_size - half_gap
            y2 = self.margin + (k.y + k.height) * self.u_size - half_gap

            color = frame.get_color(k.led_slot)

            # Draw keycap body
            draw.rounded_rectangle(
                [x1, y1, x2, y2],
                radius=rad,
                fill=color,
                outline=self.border_color,
                width=1,
            )

            # Optional key label (contrasting color based on key brightness)
            if labels_enabled:
                lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
                text_color = (30, 30, 30) if lum > 140 else (220, 220, 220)
                # Simple fallback text drawing without requiring external font files
                short_lbl = k.label[:4]
                # Center label roughly
                text_x = (x1 + x2) / 2.0
                text_y = (y1 + y2) / 2.0
                draw.text(
                    (text_x, text_y),
                    short_lbl,
                    fill=text_color,
                    anchor="mm",
                )

        return img
