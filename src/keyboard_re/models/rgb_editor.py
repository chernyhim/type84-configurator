"""
High-Level RGB Global Editor & Builder for IO by Red Square Type 84 Magnetic Black.

Encapsulates declarative parameter editing, validation against RGBEffectMeta,
fluent method chaining, and clean separation between persistent state and runtime visuals.
NO physical HID writes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from keyboard_re.protocol.rgb import (
    EFFECT_HEARTBEAT,
    EFFECT_OFF,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    RGB_EFFECT_CATALOG,
    RGBEffectMeta,
    RGBGlobalConfig,
    get_effect_meta,
    validate_rgb_config,
)


def parse_color_input(color: Union[Tuple[int, int, int], Sequence[int], str]) -> Tuple[int, int, int]:
    """
    Parse a flexible color input (tuple, sequence, or hex string) into a (R, G, B) uint8 tuple.
    
    Supported formats:
    - (255, 128, 0)
    - [255, 128, 0]
    - "#FF8000"
    - "FF8000"
    - "#f00" (expanded to "#FF0000")
    """
    if isinstance(color, str):
        s = color.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", s):
            raise ValueError(f"Invalid hex color string: '{color}'")
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (r, g, b)

    if isinstance(color, (tuple, list, Sequence)):
        if len(color) != 3:
            raise ValueError(f"Color sequence must have exactly 3 components, got {len(color)}")
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        for name, val in [("Red", r), ("Green", g), ("Blue", b)]:
            if not (0 <= val <= 255):
                raise ValueError(f"{name} channel {val} out of range [0, 255]")
        return (r, g, b)

    raise TypeError(f"Unsupported color type: {type(color)}")


def color_to_hex(color: Tuple[int, int, int]) -> str:
    """Format a 3-tuple (R, G, B) into a hex string '#RRGGBB'."""
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


class RGBGlobalEditor:
    """
    Fluent builder and state-editor for an RGBGlobalConfig.
    
    Guarantees:
    - Pure in-memory configuration;
    - Deterministic persistent state generation (AA 13/AA 23);
    - Direct access to available control capabilities for UI rendering;
    - Explicit validation with clear error messages.
    """

    def __init__(
        self,
        effect: int = EFFECT_STATIC,
        primary: Tuple[int, int, int] = (255, 255, 255),
        secondary: Tuple[int, int, int] = (0, 0, 0),
        brightness: int = 5,
        speed: int = 3,
        color_mode: int = 1,
        direction: int = 0,
        effect_mode_type: int = 0,
        driver_setting: int = 255,
    ) -> None:
        self._effect = effect
        self._primary = primary
        self._secondary = secondary
        self._brightness = brightness
        self._speed = speed
        self._color_mode = color_mode
        self._direction = direction
        self._effect_mode_type = effect_mode_type
        self._driver_setting = driver_setting

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def effect(self) -> int:
        return self._effect

    @property
    def meta(self) -> Optional[RGBEffectMeta]:
        return get_effect_meta(self._effect)

    @property
    def primary(self) -> Tuple[int, int, int]:
        return self._primary

    @property
    def primary_hex(self) -> str:
        return color_to_hex(self._primary)

    @property
    def secondary(self) -> Tuple[int, int, int]:
        return self._secondary

    @property
    def secondary_hex(self) -> str:
        return color_to_hex(self._secondary)

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def speed(self) -> int:
        return self._speed

    @property
    def color_mode(self) -> int:
        return self._color_mode

    @property
    def is_single_color(self) -> bool:
        return self._color_mode == 0

    @property
    def is_rainbow(self) -> bool:
        return self._color_mode == 1

    @property
    def direction(self) -> int:
        return self._direction

    @property
    def effect_mode_type(self) -> int:
        return self._effect_mode_type

    @property
    def driver_setting(self) -> int:
        return self._driver_setting

    # -------------------------------------------------------------------------
    # UI Capability Flags
    # -------------------------------------------------------------------------

    @property
    def available_controls(self) -> Dict[str, bool]:
        """Dictionary of available UI controls for the current effect."""
        m = self.meta
        if m is None:
            return {
                "color": True,
                "color_mode": True,
                "speed": True,
                "direction": False,
                "secondary_color": False,
                "submodes": False,
            }
        return {
            "color": m.supports_color,
            "color_mode": m.supports_color_mode,
            "speed": m.supports_speed,
            "direction": m.supports_direction,
            "secondary_color": m.supports_secondary_color,
            "submodes": m.supports_submodes,
        }

    @property
    def direction_labels(self) -> Tuple[str, str]:
        """Direction labels according to effect metadata (e.g. Top/Bottom or Left/Right)."""
        m = self.meta
        if m:
            return m.direction_labels
        return ("Left", "Right")

    @property
    def submode_options(self) -> Dict[int, str]:
        """Dictionary of submode IDs to descriptive names for current effect."""
        m = self.meta
        if m:
            return m.submode_names
        return {}

    # -------------------------------------------------------------------------
    # Fluent Mutators
    # -------------------------------------------------------------------------

    def set_effect(
        self,
        effect: Union[int, str],
        normalize_unsupported: bool = True,
    ) -> RGBGlobalEditor:
        """
        Switch active effect mode by integer ID or by name (EN or RU, case-insensitive).
        
        If normalize_unsupported is True, parameters not supported by the new effect
        (such as direction, secondary color, or submode) are sanitized to safe defaults.
        """
        resolved_id: Optional[int] = None
        if isinstance(effect, int):
            resolved_id = effect
        elif isinstance(effect, str):
            eff_clean = effect.strip().lower()
            if eff_clean.startswith("0x"):
                try:
                    resolved_id = int(eff_clean, 16)
                except ValueError:
                    pass
            elif eff_clean.isdigit():
                resolved_id = int(eff_clean)

            if resolved_id is None:
                for eid, m in RGB_EFFECT_CATALOG.items():
                    if eff_clean in (m.name_en.lower(), m.name_ru.lower()):
                        resolved_id = eid
                        break

        if resolved_id is None:
            raise ValueError(f"Could not resolve effect mode from: '{effect}'")

        self._effect = resolved_id
        meta = get_effect_meta(self._effect)

        if normalize_unsupported and meta is not None:
            if not meta.supports_direction:
                self._direction = 0
            if not meta.supports_secondary_color:
                self._secondary = (0, 0, 0)
            if not meta.supports_submodes:
                self._effect_mode_type = 0
            if not meta.supports_color_mode:
                self._color_mode = 0

        return self

    def set_primary_color(
        self,
        color_or_r: Union[Tuple[int, int, int], Sequence[int], str, int],
        g: Optional[int] = None,
        b: Optional[int] = None,
    ) -> RGBGlobalEditor:
        """Set primary RGB color."""
        if g is not None and b is not None and isinstance(color_or_r, int):
            self._primary = parse_color_input((color_or_r, g, b))
        else:
            self._primary = parse_color_input(color_or_r)  # type: ignore[arg-type]
        return self

    def set_secondary_color(
        self,
        color_or_r: Union[Tuple[int, int, int], Sequence[int], str, int],
        g: Optional[int] = None,
        b: Optional[int] = None,
    ) -> RGBGlobalEditor:
        """Set secondary RGB color (for dual-color effects such as Heartbeat)."""
        if g is not None and b is not None and isinstance(color_or_r, int):
            self._secondary = parse_color_input((color_or_r, g, b))
        else:
            self._secondary = parse_color_input(color_or_r)  # type: ignore[arg-type]
        return self

    def set_single_color(
        self,
        color: Optional[Union[Tuple[int, int, int], Sequence[int], str]] = None,
    ) -> RGBGlobalEditor:
        """
        Switch to Single Color mode (color_mode = 0).
        Optionally updates primary color if provided.
        """
        self._color_mode = 0
        if color is not None:
            self.set_primary_color(color)
        return self

    def set_rainbow(self) -> RGBGlobalEditor:
        """
        Switch to Rainbow RGB mode (color_mode = 1).
        Sets primary color to #FFFFFF as standard for multicolor.
        """
        self._color_mode = 1
        self._primary = (255, 255, 255)
        return self

    def set_brightness(self, level: int) -> RGBGlobalEditor:
        """Set brightness level (0..5)."""
        if not (0 <= level <= 5):
            raise ValueError(f"Brightness {level} out of range [0, 5]")
        self._brightness = level
        return self

    def set_speed(self, level: int) -> RGBGlobalEditor:
        """Set speed level (1..5)."""
        if not (1 <= level <= 5):
            raise ValueError(f"Speed {level} out of range [1, 5]")
        self._speed = level
        return self

    def set_direction(self, direction: int) -> RGBGlobalEditor:
        """Set direction (0 or 1, or 2/3 for directional effects)."""
        if direction not in (0, 1, 2, 3):
            raise ValueError(f"Direction {direction} out of range [0, 3]")
        self._direction = direction
        return self

    def set_heartbeat(
        self,
        submode: int = 0,
        primary: Optional[Union[Tuple[int, int, int], str]] = None,
        secondary: Optional[Union[Tuple[int, int, int], str]] = None,
        speed: int = 5,
        brightness: int = 5,
    ) -> RGBGlobalEditor:
        """Configure Heartbeat mode (0xFE) with primary & secondary colors and submode."""
        self._effect = EFFECT_HEARTBEAT
        self._color_mode = 0
        self._effect_mode_type = submode
        self._speed = speed
        self._brightness = brightness
        if primary is not None:
            self.set_primary_color(primary)
        if secondary is not None:
            self.set_secondary_color(secondary)
        return self

    def set_single_color_ripple(
        self,
        color: Union[Tuple[int, int, int], Sequence[int], str] = (255, 128, 0),
        brightness: int = 5,
        speed: int = 5,
    ) -> RGBGlobalEditor:
        """
        Configure Single-Color Reactive Ripple (Effect 0x0F) deterministically.
        
        Solves the forensic discrepancy: explicitly specifies color_mode = 0 and
        primary RGB so that hardware renders the desired solid color ripple.
        """
        self._effect = EFFECT_RIPPLE_SPREAD
        self._color_mode = 0
        self._primary = parse_color_input(color)
        self._secondary = (0, 0, 0)
        self._brightness = brightness
        self._speed = speed
        self._direction = 0
        self._effect_mode_type = 0
        return self

    # -------------------------------------------------------------------------
    # Validation & Build
    # -------------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Pure validation returning list of error strings (empty if valid)."""
        cfg = self.build_unchecked()
        return validate_rgb_config(cfg)

    def is_valid(self) -> bool:
        """Returns True if the current configuration is valid."""
        return len(self.validate()) == 0

    def build_unchecked(self) -> RGBGlobalConfig:
        """Build RGBGlobalConfig instance without raising on validation errors."""
        return RGBGlobalConfig(
            effect=self._effect,
            primary=self._primary,
            secondary=self._secondary,
            brightness=self._brightness,
            speed=self._speed,
            color_mode=self._color_mode,
            direction=self._direction,
            effect_mode_type=self._effect_mode_type,
            driver_setting=self._driver_setting,
        )

    def build(self) -> RGBGlobalConfig:
        """
        Validate and build an immutable RGBGlobalConfig.
        Raises ValueError if configuration is invalid.
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"Cannot build RGBGlobalConfig: {'; '.join(errors)}")
        return self.build_unchecked()

    # -------------------------------------------------------------------------
    # Serialization / Factory
    # -------------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: RGBGlobalConfig) -> RGBGlobalEditor:
        """Create an editor populated from an existing RGBGlobalConfig."""
        return cls(
            effect=config.effect,
            primary=config.primary,
            secondary=config.secondary,
            brightness=config.brightness,
            speed=config.speed,
            color_mode=config.color_mode,
            direction=config.direction,
            effect_mode_type=config.effect_mode_type,
            driver_setting=config.driver_setting,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Export editor state to dictionary."""
        return self.build_unchecked().to_dict()

    def clone(self) -> RGBGlobalEditor:
        """Return an independent copy of this editor."""
        return RGBGlobalEditor(
            effect=self._effect,
            primary=self._primary,
            secondary=self._secondary,
            brightness=self._brightness,
            speed=self._speed,
            color_mode=self._color_mode,
            direction=self._direction,
            effect_mode_type=self._effect_mode_type,
            driver_setting=self._driver_setting,
        )

    def __repr__(self) -> str:
        m = self.meta
        name = m.name_en if m else f"0x{self._effect:02X}"
        mode_str = "Rainbow" if self._color_mode == 1 else f"Single({self.primary_hex})"
        return (
            f"RGBGlobalEditor(effect='{name}', mode={mode_str}, "
            f"brightness={self._brightness}, speed={self._speed})"
        )
