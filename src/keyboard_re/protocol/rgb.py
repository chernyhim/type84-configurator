"""
RGB Protocol Encoder & Decoder (AA 23 Global & AA 24 Per-Key).

Pure offline protocol implementation for building and parsing:
- Global RGB configuration reports: AA 23 10 ... AA 55
- 512-byte Per-Key LED buffers: slot i = [R, G, B, LED_ID]
- Per-Key LED report sequences (9 chunks x 56 bytes + 1 tail x 8 bytes)
- Physical LED layout mapping (strictly decoupled from Hall switch matrix)

NO hardware transmission or direct HID write operations.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from keyboard_re.protocol.packets import REPORT_SIZE, pad_report, validate_report_size

# Global RGB protocol constants
RGB_GLOBAL_PREFIX: bytes = b"\xAA\x23\x10"
RGB_GLOBAL_HEADER_RESERVED: bytes = b"\x00\x00\x00\x01\x00"
RGB_GLOBAL_MAGIC: bytes = b"\xAA\x55"

# All 26 Confirmed Effect Mode IDs (AA 23 Byte 8)
EFFECT_OFF: int = 0x00
EFFECT_STATIC: int = 0x01
EFFECT_SINGLE_KEY_ON: int = 0x02
EFFECT_SINGLE_KEY_OFF: int = 0x03
EFFECT_STARS_TWINKLE: int = 0x04
EFFECT_SNOWFALL: int = 0x05
EFFECT_BLOOMING_FLOWERS: int = 0x06
EFFECT_BREATHING: int = 0x07
EFFECT_SPECTRUM: int = 0x08
EFFECT_COLOR_SPRING: int = 0x09
EFFECT_CRISS_CROSS: int = 0x0A
EFFECT_WAVE: int = 0x0B
EFFECT_TURNING_PEAK: int = 0x0C
EFFECT_TRIGGER_WAVE: int = 0x0D
EFFECT_BOUNCE_RIPPLE: int = 0x0E
EFFECT_RIPPLE_SPREAD: int = 0x0F  # Reactive Ripple
EFFECT_ENDLESS_STREAM: int = 0x10
EFFECT_OVERLAPPING_PEAKS: int = 0x11
EFFECT_WIND_AND_RAIN: int = 0x12
EFFECT_SHUTTLE: int = 0x13
EFFECT_RAINBOW_WINDMILL: int = 0x17
EFFECT_COLORFUL_CONVERGENCE: int = 0x18
EFFECT_NEON_OVERLAY: int = 0x19
EFFECT_CUSTOM: int = 0x80
EFFECT_HEARTBEAT: int = 0xFE
EFFECT_GIF: int = 0xFF

# Vendor firmware readback/status aliases for Custom Mode (Effect 0x80).
# When the device transitions to Custom Per-Key mode (0x80), the hardware
# status register (AA 13 -> 55 13) returns 0x14 (or 0x15 on some firmware revisions)
# as an active-mode status code. These are NOT write-side effect IDs and MUST NOT
# be added to RGB_EFFECT_CATALOG.
EFFECT_CUSTOM_READBACK_ALIASES = frozenset({0x80, 0x14, 0x15})

# Firmware Quirk / Capability:
# For effect 0x0F (EFFECT_RIPPLE_SPREAD), keyboard firmware physically ignores
# primary_color and color_mode on AA 23 write and always returns in AA 13 readback:
# primary_color = (255, 255, 255) (#FFFFFF) and color_mode = 1 (Rainbow RGB).
# Readback verification treats these runtime status fields as equivalent.
EFFECTS_WITH_FIXED_RAINBOW_READBACK: frozenset[int] = frozenset({EFFECT_RIPPLE_SPREAD})


def is_effect_fixed_rainbow_readback(effect_id: int) -> bool:
    """
    Check whether an effect has the firmware quirk where readback always reports
    rainbow mode (primary=#FFFFFF, color_mode=1) regardless of write values.
    """
    return effect_id in EFFECTS_WITH_FIXED_RAINBOW_READBACK


def is_custom_effect_readback_equivalent(target_effect: int, readback_effect: int) -> bool:
    """
    Check whether a readback effect mode is equivalent to a target effect mode.
    For Custom Mode (0x80), firmware returns 0x14 (or 0x15) as runtime status.
    For all standard effects, exact equality is required.
    """
    if target_effect == EFFECT_CUSTOM or readback_effect == EFFECT_CUSTOM:
        return (
            target_effect in EFFECT_CUSTOM_READBACK_ALIASES
            and readback_effect in EFFECT_CUSTOM_READBACK_ALIASES
        )
    return target_effect == readback_effect


@dataclass(frozen=True)
class RGBEffectMeta:
    """Declarative metadata for an RGB effect mode."""
    id: int
    name_en: str
    name_ru: str
    category: str  # "standard", "extended", "parametric", "special"
    has_speed: bool
    has_direction: bool
    has_color: bool
    has_color_mode: bool  # Supports switching between Single Color (0) and Rainbow RGB (1)
    has_secondary_color: bool = False
    sub_modes: Tuple[str, ...] = ()
    direction_position: Optional[str] = None
    default_speed: int = 3
    is_special_mode: bool = False
    firmware_fixed_rainbow_readback: bool = False

    @property
    def supports_color(self) -> bool:
        return self.has_color

    @property
    def supports_color_mode(self) -> bool:
        return self.has_color_mode

    @property
    def supports_speed(self) -> bool:
        return self.has_speed

    @property
    def supports_direction(self) -> bool:
        return self.has_direction

    @property
    def supports_secondary_color(self) -> bool:
        return self.has_secondary_color

    @property
    def supports_submodes(self) -> bool:
        return len(self.sub_modes) > 0

    @property
    def direction_labels(self) -> Tuple[str, str]:
        if self.direction_position == "top":
            return ("Top", "Bottom")
        return ("Left", "Right")

    @property
    def submode_names(self) -> Dict[int, str]:
        return {idx: name for idx, name in enumerate(self.sub_modes)}


# Declarative Registry of all 26 confirmed hardware & operational modes
RGB_EFFECT_CATALOG: Dict[int, RGBEffectMeta] = {
    0x00: RGBEffectMeta(
        id=0x00, name_en="Off", name_ru="Выключено",
        category="special", has_speed=False, has_direction=False,
        has_color=False, has_color_mode=False, is_special_mode=True,
    ),
    0x01: RGBEffectMeta(
        id=0x01, name_en="Static Always On", name_ru="Статичное свечение",
        category="standard", has_speed=False, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x02: RGBEffectMeta(
        id=0x02, name_en="Single Key On", name_ru="Точечное включение",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x03: RGBEffectMeta(
        id=0x03, name_en="Single Key Off", name_ru="Точечное гашение",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x04: RGBEffectMeta(
        id=0x04, name_en="Stars Twinkling", name_ru="Звёздное небо",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x05: RGBEffectMeta(
        id=0x05, name_en="Snowfall", name_ru="Снегопад",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x06: RGBEffectMeta(
        id=0x06, name_en="Blooming Flowers", name_ru="Цветущий сад",
        category="standard", has_speed=True, has_direction=False,
        has_color=False, has_color_mode=False,
    ),
    0x07: RGBEffectMeta(
        id=0x07, name_en="Dynamic Breathing", name_ru="Дыхание",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x08: RGBEffectMeta(
        id=0x08, name_en="Spectrum Cycle", name_ru="Спектр",
        category="standard", has_speed=True, has_direction=False,
        has_color=False, has_color_mode=False,
    ),
    0x09: RGBEffectMeta(
        id=0x09, name_en="Color Spring", name_ru="Фонтан",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x0A: RGBEffectMeta(
        id=0x0A, name_en="Color Criss-Cross", name_ru="Перекрёстный",
        category="standard", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, direction_position="top",
    ),
    0x0B: RGBEffectMeta(
        id=0x0B, name_en="Wave", name_ru="Волна",
        category="standard", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, direction_position="left",
    ),
    0x0C: RGBEffectMeta(
        id=0x0C, name_en="Turning Peak", name_ru="Вихрь",
        category="standard", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, direction_position="left",
    ),
    0x0D: RGBEffectMeta(
        id=0x0D, name_en="Trigger Wave", name_ru="Лавина",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x0E: RGBEffectMeta(
        id=0x0E, name_en="Bounce Ripple", name_ru="Рикошет",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x0F: RGBEffectMeta(
        id=0x0F, name_en="Ripple Spread", name_ru="Расходящаяся рябь",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
        firmware_fixed_rainbow_readback=True,
    ),
    0x10: RGBEffectMeta(
        id=0x10, name_en="Endless Stream", name_ru="Поток",
        category="standard", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, direction_position="left",
    ),
    0x11: RGBEffectMeta(
        id=0x11, name_en="Overlapping Peaks", name_ru="Эхо",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x12: RGBEffectMeta(
        id=0x12, name_en="Wind and Rain", name_ru="Косой дождь",
        category="standard", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, direction_position="left",
    ),
    0x13: RGBEffectMeta(
        id=0x13, name_en="Shuttle", name_ru="Маятник",
        category="standard", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True,
    ),
    0x17: RGBEffectMeta(
        id=0x17, name_en="Rainbow Windmill", name_ru="Ветряк",
        category="extended", has_speed=True, has_direction=True,
        has_color=False, has_color_mode=False, default_speed=5,
    ),
    0x18: RGBEffectMeta(
        id=0x18, name_en="Colorful Convergence", name_ru="Конвергенция",
        category="extended", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=True, default_speed=5,
    ),
    0x19: RGBEffectMeta(
        id=0x19, name_en="Neon Shadow", name_ru="Неоновые тени",
        category="extended", has_speed=True, has_direction=True,
        has_color=True, has_color_mode=True, default_speed=5,
    ),
    0x80: RGBEffectMeta(
        id=0x80, name_en="Custom Per-Key", name_ru="Поканальная матрица",
        category="special", has_speed=False, has_direction=False,
        has_color=True, has_color_mode=False, is_special_mode=True,
    ),
    0xFE: RGBEffectMeta(
        id=0xFE, name_en="Heartbeat", name_ru="Биение сердца",
        category="parametric", has_speed=True, has_direction=False,
        has_color=True, has_color_mode=False, has_secondary_color=True,
        sub_modes=("heart_breathing", "background_breathing", "both_breathing", "constant"),
        default_speed=5,
    ),
    0xFF: RGBEffectMeta(
        id=0xFF, name_en="GIF Animation", name_ru="GIF анимация",
        category="special", has_speed=True, has_direction=False,
        has_color=False, has_color_mode=False, is_special_mode=True,
    ),
}


def get_effect_meta(effect_id: int) -> Optional[RGBEffectMeta]:
    """Get metadata for an RGB effect mode ID."""
    return RGB_EFFECT_CATALOG.get(effect_id)


# Per-Key RGB protocol dimensions
LED_SLOT_COUNT: int = 128
LED_SLOT_SIZE: int = 4
LED_BUFFER_SIZE: int = 512  # 128 slots * 4 bytes = 512 bytes

RGB_PER_KEY_CHUNK_PREFIX: bytes = b"\xAA\x24\x38"
RGB_PER_KEY_CHUNK_PAYLOAD_SIZE: int = 56
RGB_PER_KEY_CHUNK_COUNT: int = 9  # 9 * 56 = 504 bytes

RGB_PER_KEY_TAIL_PREFIX: bytes = b"\xAA\x24\x08"
RGB_PER_KEY_TAIL_ADDR: int = 0x01F8  # 504 decimal
RGB_PER_KEY_TAIL_PAYLOAD_SIZE: int = 8  # 8 bytes (slots 126 & 127)
RGB_PER_KEY_TOTAL_REPORTS: int = 10  # 9 standard chunks + 1 tail


@dataclass
class RGBGlobalConfig:
    """Parsed model of a 64-byte AA 23 10 Global RGB packet."""
    effect: int
    primary: Tuple[int, int, int]
    secondary: Tuple[int, int, int] = (0, 0, 0)
    brightness: int = 5
    speed: int = 3
    color_mode: int = 1
    direction: int = 0
    effect_mode_type: int = 0
    driver_setting: int = 255
    reserved_header: bytes = RGB_GLOBAL_HEADER_RESERVED
    reserved_byte21: int = 0x00
    magic: bytes = RGB_GLOBAL_MAGIC
    raw_payload: Optional[bytes] = None
    reserved_mid: bytes = b"\x00\x00"
    reserved_tail: bytes = b"\x00\x00\x00"

    @property
    def is_custom_mode(self) -> bool:
        """True if the effect is Per-Key custom lighting (0x80)."""
        return self.effect == EFFECT_CUSTOM

    @property
    def meta(self) -> Optional[RGBEffectMeta]:
        """Declarative effect metadata from catalog."""
        return RGB_EFFECT_CATALOG.get(self.effect)

    def to_dict(self) -> Dict[str, Any]:
        """Export to dictionary representation."""
        return {
            "effect": self.effect,
            "effect_name": self.meta.name_en if self.meta else f"Unknown (0x{self.effect:02X})",
            "primary": list(self.primary),
            "secondary": list(self.secondary),
            "color_mode": self.color_mode,
            "brightness": self.brightness,
            "speed": self.speed,
            "direction": self.direction,
            "effect_mode_type": self.effect_mode_type,
            "driver_setting": self.driver_setting,
            "raw_hex": self.raw_payload.hex() if self.raw_payload else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RGBGlobalConfig:
        """Reconstruct model from dictionary representation."""
        raw_bytes = bytes.fromhex(data["raw_hex"]) if data.get("raw_hex") else None
        return cls(
            effect=data.get("effect", 0),
            primary=tuple(data.get("primary", (0, 0, 0))),
            secondary=tuple(data.get("secondary", (0, 0, 0))),
            brightness=data.get("brightness", 5),
            speed=data.get("speed", 3),
            color_mode=data.get("color_mode", 1),
            direction=data.get("direction", 0),
            effect_mode_type=data.get("effect_mode_type", 0),
            driver_setting=data.get("driver_setting", 255),
            raw_payload=raw_bytes,
        )

    def validate(self, catalog: Optional[Dict[int, RGBEffectMeta]] = None) -> List[str]:
        """Pure validation returning list of errors (empty if valid)."""
        return validate_rgb_config(self, catalog=catalog)

    @property
    def is_valid(self) -> bool:
        """True if configuration is completely valid with zero errors."""
        return len(self.validate()) == 0


def validate_rgb_config(
    config: RGBGlobalConfig,
    catalog: Optional[Dict[int, RGBEffectMeta]] = None,
) -> List[str]:
    """
    Pure validation function for RGBGlobalConfig against hardware constraints and catalog.
    
    Returns a list of error strings (empty list indicates valid configuration).
    Does NOT mutate or silently normalize the input configuration.
    """
    errors: List[str] = []
    reg = catalog if catalog is not None else RGB_EFFECT_CATALOG

    # 1. Effect mode ID
    if not (0 <= config.effect <= 255):
        errors.append(f"Effect mode ID {config.effect} out of byte range [0, 255]")
        return errors

    meta = reg.get(config.effect)
    if meta is None:
        errors.append(f"Unknown effect mode ID: 0x{config.effect:02X} ({config.effect})")
        return errors

    # 2. Brightness (Hardware confirmed range: 0..5)
    if not (0 <= config.brightness <= 5):
        errors.append(f"Brightness {config.brightness} out of valid range [0, 5]")

    # 3. Speed (Hardware confirmed range: 1..5)
    if not (1 <= config.speed <= 5):
        errors.append(f"Speed {config.speed} out of valid range [1, 5]")

    # 4. Color Mode (0 = Single Color, 1 = Rainbow RGB)
    if config.color_mode not in (0, 1):
        errors.append(f"Color mode {config.color_mode} invalid (must be 0 for Single Color or 1 for Rainbow RGB)")

    # 5. Primary color channels (uint8: 0..255)
    if len(config.primary) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in config.primary):
        errors.append(f"Primary color {config.primary} must be a 3-tuple of uint8 (0..255)")

    # 6. Secondary color channels (uint8: 0..255)
    if len(config.secondary) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in config.secondary):
        errors.append(f"Secondary color {config.secondary} must be a 3-tuple of uint8 (0..255)")
    elif not meta.has_secondary_color and config.secondary != (0, 0, 0):
        errors.append(
            f"Effect 0x{config.effect:02X} ({meta.name_en}) does not support secondary color, but secondary={config.secondary}"
        )

    # 7. Direction
    if config.direction not in (0, 1, 2, 3):
        errors.append(f"Direction {config.direction} invalid (must be in range [0, 3])")
    elif not meta.has_direction and config.direction != 0:
        errors.append(
            f"Effect 0x{config.effect:02X} ({meta.name_en}) does not support direction, but direction={config.direction}"
        )

    # 8. Submodes / effect_mode_type
    if meta.sub_modes:
        if not (0 <= config.effect_mode_type < len(meta.sub_modes)):
            errors.append(
                f"Effect 0x{config.effect:02X} ({meta.name_en}) effect_mode_type {config.effect_mode_type} "
                f"out of valid submode range [0, {len(meta.sub_modes) - 1}]"
            )
    else:
        if config.effect_mode_type != 0:
            errors.append(
                f"Effect 0x{config.effect:02X} ({meta.name_en}) does not support submodes, but effect_mode_type={config.effect_mode_type}"
            )

    # 9. Driver setting (host write: 255 or 0)
    if not (0 <= config.driver_setting <= 255):
        errors.append(f"Driver setting {config.driver_setting} out of valid range [0, 255]")

    return errors


def build_rgb_global(
    effect: int,
    primary: Tuple[int, int, int],
    secondary: Tuple[int, int, int] = (0, 0, 0),
    brightness: int = 5,
    speed: int = 3,
    reserved_header: bytes = RGB_GLOBAL_HEADER_RESERVED,
    reserved_mid: bytes = b"\x00\x00",
    reserved_tail: bytes = b"\x00\x00\x00",
    color_mode: Optional[int] = None,
    direction: Optional[int] = None,
    effect_mode_type: Optional[int] = None,
    driver_setting: Optional[int] = None,
    reserved_byte21: Optional[int] = None,
    magic: bytes = RGB_GLOBAL_MAGIC,
    raw_payload: Optional[bytes] = None,
) -> bytes:
    """
    Build a 64-byte Global RGB HID output report (AA 23 10).

    Structure:
    - Bytes 0..2: AA 23 10 (Prefix)
    - Bytes 3..7: Reserved header (00 00 00 01 00)
    - Byte 8: Effect Mode ID (0x00..0xFF)
    - Bytes 9..11: Primary Color (R, G, B: uint8)
    - Byte 12: Driver Setting (uint8, 0xFF on host write)
    - Bytes 13..15: Secondary Color (R, G, B: uint8)
    - Byte 16: Color Mode (uint8, 0=Single Color, 1=Rainbow RGB)
    - Byte 17: Brightness (uint8: 0..5)
    - Byte 18: Speed (uint8: 1..5)
    - Byte 19: Direction (uint8: 0=Left/Top, 1=Right/Bottom)
    - Byte 20: Effect Mode Type (uint8: sub-mode for 0xFE Heartbeat)
    - Byte 21: Reserved byte (0x00)
    - Bytes 22..23: Magic signature (AA 55)
    - Bytes 24..63: Zero padding (40 bytes)
    Total length: exactly 64 bytes.
    """
    if raw_payload is not None and len(raw_payload) == REPORT_SIZE and raw_payload.startswith(RGB_GLOBAL_PREFIX):
        return bytes(raw_payload)

    if not (0 <= effect <= 255):
        raise ValueError(f"Effect mode {effect} out of range [0, 255]")
    for name, col in [("Primary", primary), ("Secondary", secondary)]:
        if len(col) != 3 or not all(0 <= c <= 255 for c in col):
            raise ValueError(f"{name} color {col} must be a 3-tuple in range [0, 255]")
    if not (0 <= brightness <= 5):
        raise ValueError(f"Brightness {brightness} out of range [0, 5]")
    if not (1 <= speed <= 5):
        raise ValueError(f"Speed {speed} out of range [1, 5]")

    # Resolve optional fields with backwards-compatible defaults from legacy buffers:
    if secondary == (0, 0, 0) and len(reserved_mid) >= 1 and reserved_mid[0] != 0:
        secondary = (0, 0, reserved_mid[0])
    if color_mode is None:
        color_mode = reserved_mid[1] if len(reserved_mid) >= 2 else 1
    if direction is None:
        direction = reserved_tail[0] if len(reserved_tail) >= 1 else 0
    if effect_mode_type is None:
        effect_mode_type = reserved_tail[1] if len(reserved_tail) >= 2 else 0
    if reserved_byte21 is None:
        reserved_byte21 = reserved_tail[2] if len(reserved_tail) >= 3 else 0
    if driver_setting is None:
        driver_setting = 255

    if not (0 <= color_mode <= 255):
        raise ValueError(f"Color mode {color_mode} out of range [0, 255]")
    if not (0 <= direction <= 255):
        raise ValueError(f"Direction {direction} out of range [0, 255]")
    if not (0 <= effect_mode_type <= 255):
        raise ValueError(f"Effect mode type {effect_mode_type} out of range [0, 255]")
    if not (0 <= driver_setting <= 255):
        raise ValueError(f"Driver setting {driver_setting} out of range [0, 255]")
    if len(reserved_header) != 5:
        raise ValueError(f"Reserved header must be 5 bytes, got {len(reserved_header)}")

    payload = bytearray(REPORT_SIZE)
    payload[0:3] = RGB_GLOBAL_PREFIX
    payload[3:8] = reserved_header
    payload[8] = effect & 0xFF
    payload[9:12] = bytes(primary)
    payload[12] = driver_setting & 0xFF
    payload[13:16] = bytes(secondary)
    payload[16] = color_mode & 0xFF
    payload[17] = brightness & 0xFF
    payload[18] = speed & 0xFF
    payload[19] = direction & 0xFF
    payload[20] = effect_mode_type & 0xFF
    payload[21] = reserved_byte21 & 0xFF
    payload[22:24] = magic

    # NOTE: reserved_mid (bytes 15:17) corresponds to secondary[2] (Blue) and color_mode.
    # reserved_tail (bytes 19:22) corresponds to direction, effect_mode_type, and reserved_byte21.
    # Semantic fields written above MUST NOT be overwritten by legacy raw byte chunks.

    return bytes(payload)


def parse_rgb_global(report: bytes | bytearray) -> RGBGlobalConfig:
    """
    Parse a 64-byte Global RGB report (AA 23 10) into an RGBGlobalConfig model.
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(RGB_GLOBAL_PREFIX):
        raise ValueError(
            f"Invalid Global RGB prefix: expected {RGB_GLOBAL_PREFIX.hex().upper()}, got {rep[:3].hex().upper()}"
        )
    if rep[22:24] != RGB_GLOBAL_MAGIC:
        raise ValueError(
            f"Invalid Global RGB magic signature: expected {RGB_GLOBAL_MAGIC.hex().upper()}, got {rep[22:24].hex().upper()}"
        )

    res_header = rep[3:8]
    effect = rep[8]
    prim = (rep[9], rep[10], rep[11])
    drv_setting = rep[12]
    sec = (rep[13], rep[14], rep[15])
    color_mode = rep[16]
    bright = rep[17]
    spd = rep[18]
    direction = rep[19]
    effect_mode_type = rep[20]
    byte21 = rep[21]
    magic = rep[22:24]

    return RGBGlobalConfig(
        effect=effect,
        primary=prim,
        secondary=sec,
        brightness=bright,
        speed=spd,
        color_mode=color_mode,
        direction=direction,
        effect_mode_type=effect_mode_type,
        driver_setting=drv_setting,
        reserved_header=res_header,
        reserved_byte21=byte21,
        magic=magic,
        raw_payload=rep,
        reserved_mid=rep[15:17],
        reserved_tail=rep[19:22],
    )


def build_led_buffer(
    colors: Sequence[Tuple[int, int, int]] | Dict[int, Tuple[int, int, int]]
) -> bytes:
    """
    Build a 512-byte Per-Key LED buffer (128 slots x 4 bytes).

    Slot format confirmed by hardware capture:
      slot i = [Red (uint8), Green (uint8), Blue (uint8), LED_ID = i (uint8)]
    """
    buf = bytearray(LED_BUFFER_SIZE)
    color_map: Dict[int, Tuple[int, int, int]] = {}

    if isinstance(colors, dict):
        color_map = colors
    else:
        for idx, col in enumerate(colors):
            if idx < LED_SLOT_COUNT:
                color_map[idx] = col

    for i in range(LED_SLOT_COUNT):
        offset = i * LED_SLOT_SIZE
        r, g, b = color_map.get(i, (0, 0, 0))
        buf[offset : offset + 4] = bytes([r & 0xFF, g & 0xFF, b & 0xFF, i & 0xFF])

    return bytes(buf)


def parse_led_buffer(buffer: bytes | bytearray) -> Dict[int, Tuple[int, int, int]]:
    """
    Parse a 512-byte Per-Key LED buffer into a dictionary mapping slot_id -> (R, G, B).
    Validates that each slot has LED_ID matching its slot index.
    """
    if len(buffer) != LED_BUFFER_SIZE:
        raise ValueError(
            f"LED buffer must be exactly {LED_BUFFER_SIZE} bytes, got {len(buffer)}"
        )

    result: Dict[int, Tuple[int, int, int]] = {}
    for i in range(LED_SLOT_COUNT):
        offset = i * LED_SLOT_SIZE
        r, g, b, led_id = buffer[offset : offset + 4]
        if led_id != i:
            raise ValueError(
                f"Slot #{i}: LED_ID mismatch, expected {i} (0x{i:02X}), got {led_id} (0x{led_id:02X})"
            )
        result[i] = (r, g, b)

    return result


def build_rgb_per_key_chunks(buffer: bytes | bytearray) -> List[bytes]:
    """
    Encode a 512-byte LED buffer into the confirmed 10-report write sequence.

    Structure:
    - Reports 0..8 (9 chunks):
      AA 24 38 <addr_lo> <addr_hi> 00 00 00 + 56 bytes payload = 64 bytes.
      Addresses: 0x0000, 0x0038, 0x0070, 0x00A8, 0x00E0, 0x0118, 0x0150, 0x0188, 0x01C0.
    - Report 9 (Tail chunk):
      AA 24 08 F8 01 00 00 00 + 8 bytes payload + 48 zero padding = 64 bytes.
      Address: 0x01F8 (504 dec), payload corresponds to slots 126 and 127.
    """
    if len(buffer) != LED_BUFFER_SIZE:
        raise ValueError(
            f"LED buffer must be exactly {LED_BUFFER_SIZE} bytes, got {len(buffer)}"
        )

    reports: List[bytes] = []

    # 1. First 9 chunks (56 bytes each = 504 bytes)
    for i in range(RGB_PER_KEY_CHUNK_COUNT):
        addr = i * RGB_PER_KEY_CHUNK_PAYLOAD_SIZE
        payload = bytes(buffer[addr : addr + RGB_PER_KEY_CHUNK_PAYLOAD_SIZE])
        pkt = bytearray(REPORT_SIZE)
        pkt[0:3] = RGB_PER_KEY_CHUNK_PREFIX
        struct.pack_into("<H", pkt, 3, addr)
        pkt[8 : 8 + len(payload)] = payload
        reports.append(bytes(pkt))

    # 2. Final 10th chunk (8 bytes = 0x08 at address 0x01F8 = 504)
    tail_payload = bytes(buffer[RGB_PER_KEY_TAIL_ADDR : RGB_PER_KEY_TAIL_ADDR + RGB_PER_KEY_TAIL_PAYLOAD_SIZE])
    pkt = bytearray(REPORT_SIZE)
    pkt[0:3] = RGB_PER_KEY_TAIL_PREFIX
    struct.pack_into("<H", pkt, 3, RGB_PER_KEY_TAIL_ADDR)
    pkt[6] = 0x01
    pkt[8 : 8 + len(tail_payload)] = tail_payload
    reports.append(bytes(pkt))

    return reports


def parse_rgb_per_key_chunks(reports: Sequence[bytes | bytearray]) -> bytes:
    """
    Parse a 10-report sequence into the complete 512-byte LED buffer.
    """
    if len(reports) != RGB_PER_KEY_TOTAL_REPORTS:
        raise ValueError(
            f"Expected {RGB_PER_KEY_TOTAL_REPORTS} reports for Per-Key RGB, got {len(reports)}"
        )

    buf = bytearray(LED_BUFFER_SIZE)

    # 1. Parse chunks 0..8
    for i in range(RGB_PER_KEY_CHUNK_COUNT):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if not rep.startswith(RGB_PER_KEY_CHUNK_PREFIX):
            raise ValueError(
                f"Report #{i}: invalid chunk prefix, expected {RGB_PER_KEY_CHUNK_PREFIX.hex().upper()}"
            )
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * RGB_PER_KEY_CHUNK_PAYLOAD_SIZE
        if addr != expected_addr:
            raise ValueError(
                f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}"
            )
        buf[addr : addr + RGB_PER_KEY_CHUNK_PAYLOAD_SIZE] = rep[8 : 8 + RGB_PER_KEY_CHUNK_PAYLOAD_SIZE]

    # 2. Parse tail chunk
    tail_rep = bytes(reports[9])
    validate_report_size(tail_rep, REPORT_SIZE)
    if not tail_rep.startswith(RGB_PER_KEY_TAIL_PREFIX):
        raise ValueError(
            f"Report #9: invalid tail chunk prefix, expected {RGB_PER_KEY_TAIL_PREFIX.hex().upper()}"
        )
    tail_addr = struct.unpack_from("<H", tail_rep, 3)[0]
    if tail_addr != RGB_PER_KEY_TAIL_ADDR:
        raise ValueError(
            f"Report #9: tail address mismatch, expected 0x{RGB_PER_KEY_TAIL_ADDR:04X}, got 0x{tail_addr:04X}"
        )
    buf[RGB_PER_KEY_TAIL_ADDR : RGB_PER_KEY_TAIL_ADDR + RGB_PER_KEY_TAIL_PAYLOAD_SIZE] = tail_rep[8 : 8 + RGB_PER_KEY_TAIL_PAYLOAD_SIZE]

    return bytes(buf)


# =====================================================================
# Physical LED Layout Mapping (Decoupled from Hall Switch KEY_MAP)
# =====================================================================
#
# Rule: Hall switches are addressed by electrical (Bank, Column).
#       RGB LEDs are addressed by linear PCB chain along physical rows.
#
# Physical LED sequence:
# Row 0 (16 LEDs: 0..15)
# Row 1 (17 slots: 16..32, includes split-Backspace / ISO reserve)
# Row 2 (starts at 33): Tab=33, Q=34, W=35 (CONFIRMED)
PHYSICAL_LED_MAP: Dict[str, int] = {
    # Row 0: Function Row & Top Navigation (16 LEDs: 0..15)
    "ESC": 0, "F1": 1, "F2": 2, "F3": 3, "F4": 4,
    "F5": 5, "F6": 6, "F7": 7, "F8": 8, "F9": 9,
    "F10": 10, "F11": 11, "F12": 12, "PRTSC": 13, "HOME": 14, "END": 15,

    # Row 1: Number Row & Mid Navigation (17 slots: 16..32)
    "GRAVE": 16, "1": 17, "2": 18, "3": 19, "4": 20,
    "5": 21, "6": 22, "7": 23, "8": 24, "9": 25,
    "0": 26, "MINUS": 27, "EQUAL": 28, "BACKSPACE": 29,
    "BACKSPACE_SPLIT_RESERVE": 30, "INS": 31, "PGUP": 32,

    # Row 2: QWERTY Row & Low Navigation (16 slots: 33..48)
    "TAB": 33, "Q": 34,
    "W": 35,  # CONFIRMED empirically in rgb_05_per_key.json
    "E": 36, "R": 37, "T": 38, "Y": 39, "U": 40,
    "I": 41, "O": 42, "P": 43, "LBRACKET": 44, "RBRACKET": 45,
    "BACKSLASH": 46, "DEL": 47, "PGDN": 48,

    # Row 3: Home Row (14 slots: 49..62)
    "CAPSLOCK": 49, "A": 50, "S": 51, "D": 52, "F": 53,
    "G": 54, "H": 55, "J": 56, "K": 57, "L": 58,
    "SEMICOLON": 59, "QUOTE": 60, "ENTER": 61, "ENTER_ISO_RESERVE": 62,

    # Row 4: Shift Row & Up Arrow (14 slots: 63..76)
    "LSHIFT": 63, "LSHIFT_ISO_RESERVE": 64,
    "Z": 65, "X": 66, "C": 67, "V": 68, "B": 69,
    "N": 70, "M": 71, "COMMA": 72, "PERIOD": 73, "SLASH": 74,
    "RSHIFT": 75, "UP": 76,

    # Row 5: Bottom Modifier Row & Directional Arrows (11 slots: 77..87)
    "LCTRL": 77, "LWIN": 78, "LALT": 79, "SPACE": 80,
    "RALT": 81, "FN": 82, "RCTRL": 83,
    "LEFT": 84, "DOWN": 85, "RIGHT": 86,
}

# Synonyms for user convenience
PHYSICAL_LED_ALIASES: Dict[str, str] = {
    "ESCAPE": "ESC",
    "PRINT": "PRTSC",
    "PRINT_SCREEN": "PRTSC",
    "TILDE": "GRAVE",
    "`": "GRAVE",
    "~": "GRAVE",
    "-": "MINUS",
    "=": "EQUAL",
    "[": "LBRACKET",
    "]": "RBRACKET",
    "\\": "BACKSLASH",
    ";": "SEMICOLON",
    "'": "QUOTE",
    "RETURN": "ENTER",
    ",": "COMMA",
    ".": "PERIOD",
    "/": "SLASH",
    "DELETE": "DEL",
    "INSERT": "INS",
    "PAGEUP": "PGUP",
    "PAGEDOWN": "PGDN",
    "CAPS": "CAPSLOCK",
}


def get_led_index(key_name: str) -> Optional[int]:
    """
    Get the physical LED slot index for a given key name.
    Returns None if key is not found in the physical LED layout.
    """
    upper = key_name.upper().strip()
    if upper in PHYSICAL_LED_MAP:
        return PHYSICAL_LED_MAP[upper]
    if upper in PHYSICAL_LED_ALIASES:
        canonical = PHYSICAL_LED_ALIASES[upper]
        return PHYSICAL_LED_MAP.get(canonical)
    return None
