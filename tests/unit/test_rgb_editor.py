"""
Unit tests for RGBGlobalEditor, RGBEffectMeta capabilities, and validate_rgb_config.

Pure offline tests: zero physical hardware interaction.
"""

import pytest

from keyboard_re.models.rgb_editor import (
    RGBGlobalEditor,
    color_to_hex,
    parse_color_input,
)
from keyboard_re.protocol.rgb import (
    EFFECT_BLOOMING_FLOWERS,
    EFFECT_CUSTOM,
    EFFECT_HEARTBEAT,
    EFFECT_OFF,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    EFFECT_WAVE,
    RGB_EFFECT_CATALOG,
    RGBGlobalConfig,
    build_rgb_global,
    get_effect_meta,
    validate_rgb_config,
)


class TestColorParsing:
    def test_parse_tuple(self):
        assert parse_color_input((255, 128, 0)) == (255, 128, 0)
        assert parse_color_input([0, 255, 100]) == (0, 255, 100)

    def test_parse_hex_full(self):
        assert parse_color_input("#FF8000") == (255, 128, 0)
        assert parse_color_input("00FF00") == (0, 255, 0)

    def test_parse_hex_short(self):
        assert parse_color_input("#F80") == (255, 136, 0)
        assert parse_color_input("FFF") == (255, 255, 255)

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_color_input("#XYZ123")
        with pytest.raises(ValueError):
            parse_color_input((256, 0, 0))
        with pytest.raises(ValueError):
            parse_color_input((-1, 0, 0))
        with pytest.raises(ValueError):
            parse_color_input((255, 0))
        with pytest.raises(TypeError):
            parse_color_input(12345)  # type: ignore

    def test_color_to_hex(self):
        assert color_to_hex((255, 128, 0)) == "#FF8000"
        assert color_to_hex((0, 0, 0)) == "#000000"
        assert color_to_hex((255, 255, 255)) == "#FFFFFF"


class TestRGBEffectMetaCapabilities:
    def test_capabilities_properties(self):
        meta_ripple = get_effect_meta(EFFECT_RIPPLE_SPREAD)
        assert meta_ripple is not None
        assert meta_ripple.supports_color is True
        assert meta_ripple.supports_color_mode is True
        assert meta_ripple.supports_speed is True
        assert meta_ripple.supports_direction is False
        assert meta_ripple.supports_secondary_color is False
        assert meta_ripple.supports_submodes is False

    def test_direction_capabilities(self):
        meta_wave = get_effect_meta(EFFECT_WAVE)
        assert meta_wave is not None
        assert meta_wave.supports_direction is True
        assert meta_wave.direction_labels == ("Left", "Right")

    def test_heartbeat_capabilities(self):
        meta_hb = get_effect_meta(EFFECT_HEARTBEAT)
        assert meta_hb is not None
        assert meta_hb.supports_secondary_color is True
        assert meta_hb.supports_submodes is True
        assert len(meta_hb.submode_names) == 4
        assert meta_hb.submode_names[0] == "heart_breathing"

    def test_blooming_flowers_no_color(self):
        meta_bf = get_effect_meta(EFFECT_BLOOMING_FLOWERS)
        assert meta_bf is not None
        assert meta_bf.supports_color is False
        assert meta_bf.supports_color_mode is False


class TestValidateRGBConfig:
    def test_valid_static_config(self):
        cfg = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),
            secondary=(0, 0, 0),
            brightness=5,
            speed=3,
            color_mode=0,
            direction=0,
            effect_mode_type=0,
        )
        errors = validate_rgb_config(cfg)
        assert errors == []
        assert cfg.is_valid is True

    def test_invalid_effect_id(self):
        cfg = RGBGlobalConfig(
            effect=0x99,  # Non-existent
            primary=(255, 0, 0),
        )
        errors = validate_rgb_config(cfg)
        assert any("Unknown effect mode ID" in e for e in errors)
        assert cfg.is_valid is False

    def test_speed_range_enforcement(self):
        # Speed strictly 1..5
        cfg_zero_speed = RGBGlobalConfig(effect=EFFECT_STATIC, primary=(255, 0, 0), speed=0)
        assert any("Speed 0 out of valid range [1, 5]" in e for e in cfg_zero_speed.validate())

        cfg_six_speed = RGBGlobalConfig(effect=EFFECT_STATIC, primary=(255, 0, 0), speed=6)
        assert any("Speed 6 out of valid range [1, 5]" in e for e in cfg_six_speed.validate())

        cfg_valid_speed = RGBGlobalConfig(effect=EFFECT_STATIC, primary=(255, 0, 0), speed=1)
        assert cfg_valid_speed.is_valid is True

    def test_brightness_range_enforcement(self):
        cfg_neg = RGBGlobalConfig(effect=EFFECT_STATIC, primary=(255, 0, 0), brightness=-1)
        assert any("Brightness -1 out of valid range [0, 5]" in e for e in cfg_neg.validate())

        cfg_high = RGBGlobalConfig(effect=EFFECT_STATIC, primary=(255, 0, 0), brightness=6)
        assert any("Brightness 6 out of valid range [0, 5]" in e for e in cfg_high.validate())

    def test_unsupported_direction_rejected(self):
        # Static effect does not support direction
        cfg = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),
            direction=1,
        )
        errors = validate_rgb_config(cfg)
        assert any("does not support direction" in e for e in errors)

    def test_unsupported_secondary_color_rejected(self):
        cfg = RGBGlobalConfig(
            effect=EFFECT_STATIC,
            primary=(255, 0, 0),
            secondary=(0, 255, 0),  # Not heartbeat!
        )
        errors = validate_rgb_config(cfg)
        assert any("does not support secondary color" in e for e in errors)

    def test_heartbeat_submode_validation(self):
        # Valid heartbeat
        cfg_hb = RGBGlobalConfig(
            effect=EFFECT_HEARTBEAT,
            primary=(255, 0, 0),
            secondary=(0, 0, 255),
            color_mode=0,
            effect_mode_type=2,
            speed=5,
        )
        assert cfg_hb.is_valid is True

        # Invalid submode (range is 0..3)
        cfg_bad_sub = RGBGlobalConfig(
            effect=EFFECT_HEARTBEAT,
            primary=(255, 0, 0),
            secondary=(0, 0, 255),
            color_mode=0,
            effect_mode_type=4,
            speed=5,
        )
        assert any("out of valid submode range" in e for e in cfg_bad_sub.validate())


class TestRGBGlobalEditor:
    def test_default_editor(self):
        ed = RGBGlobalEditor()
        assert ed.effect == EFFECT_STATIC
        assert ed.is_rainbow is True
        assert ed.brightness == 5
        assert ed.speed == 3
        assert ed.is_valid() is True

    def test_set_effect_by_name(self):
        ed = RGBGlobalEditor()
        ed.set_effect("Ripple Spread")
        assert ed.effect == EFFECT_RIPPLE_SPREAD

        ed.set_effect("Дыхание")
        assert ed.effect == 0x07

        ed.set_effect("0x0B")
        assert ed.effect == EFFECT_WAVE

    def test_set_effect_normalization(self):
        # Start with Wave (has direction = 1)
        ed = RGBGlobalEditor()
        ed.set_effect(EFFECT_WAVE)
        ed.set_direction(1)
        assert ed.direction == 1

        # Switch to Ripple Spread (has NO direction)
        ed.set_effect(EFFECT_RIPPLE_SPREAD, normalize_unsupported=True)
        assert ed.direction == 0  # Normalized to 0!

    def test_single_color_reactive_ripple_forensic_case(self):
        """Verify the exact fix for the forensic discrepancy."""
        ed = RGBGlobalEditor()
        ed.set_single_color_ripple(color="#FF8000", brightness=5, speed=5)

        assert ed.effect == EFFECT_RIPPLE_SPREAD
        assert ed.color_mode == 0
        assert ed.is_single_color is True
        assert ed.primary == (255, 128, 0)
        assert ed.primary_hex == "#FF8000"
        assert ed.secondary == (0, 0, 0)
        assert ed.brightness == 5
        assert ed.speed == 5
        assert ed.direction == 0
        assert ed.is_valid() is True

        # Build config
        cfg = ed.build()
        assert cfg.effect == 15
        assert cfg.color_mode == 0

        # Build wire packet
        pkt = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            secondary=cfg.secondary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            color_mode=cfg.color_mode,
            direction=cfg.direction,
            effect_mode_type=cfg.effect_mode_type,
        )
        assert len(pkt) == 64
        assert pkt[0:3] == b"\xAA\x23\x10"
        assert pkt[8] == 0x0F  # Effect
        assert pkt[9:12] == bytes([255, 128, 0])  # Primary RGB
        assert pkt[16] == 0x00  # Color Mode = 0 (SINGLE COLOR)!
        assert pkt[17] == 0x05  # Brightness
        assert pkt[18] == 0x05  # Speed

    def test_rainbow_reactive_ripple(self):
        ed = RGBGlobalEditor()
        ed.set_effect(EFFECT_RIPPLE_SPREAD).set_rainbow().set_brightness(5).set_speed(5)

        cfg = ed.build()
        assert cfg.effect == 0x0F
        assert cfg.color_mode == 1  # Rainbow!
        assert cfg.primary == (255, 255, 255)

        pkt = build_rgb_global(
            effect=cfg.effect,
            primary=cfg.primary,
            brightness=cfg.brightness,
            speed=cfg.speed,
            color_mode=cfg.color_mode,
        )
        assert pkt[8] == 0x0F
        assert pkt[16] == 0x01  # Rainbow mode!

    def test_from_config_roundtrip(self):
        cfg = RGBGlobalConfig(
            effect=EFFECT_WAVE,
            primary=(0, 255, 128),
            secondary=(0, 0, 0),
            brightness=4,
            speed=2,
            color_mode=0,
            direction=1,
            effect_mode_type=0,
        )
        ed = RGBGlobalEditor.from_config(cfg)
        assert ed.effect == EFFECT_WAVE
        assert ed.primary == (0, 255, 128)
        assert ed.brightness == 4
        assert ed.speed == 2
        assert ed.direction == 1
        assert ed.color_mode == 0

        cfg_built = ed.build()
        assert cfg_built.effect == cfg.effect
        assert cfg_built.primary == cfg.primary
        assert cfg_built.direction == cfg.direction

    def test_build_invalid_raises_value_error(self):
        ed = RGBGlobalEditor()
        ed._effect = 0x99  # directly force invalid effect
        with pytest.raises(ValueError, match="Cannot build RGBGlobalConfig"):
            ed.build()
