"""
Integration unit tests for Profile and ProfileManager RGB API.

Tests Profile RGB editing methods, ProfileManager updates,
diffing, JSON serialization, and write plan generation.
Pure offline tests: zero physical hardware interaction.
"""

from pathlib import Path
import tempfile
import pytest

from keyboard_re.models.rgb_editor import RGBGlobalEditor
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.rgb import (
    EFFECT_BREATHING,
    EFFECT_HEARTBEAT,
    EFFECT_RIPPLE_SPREAD,
    EFFECT_STATIC,
    RGBGlobalConfig,
)


@pytest.fixture
def baseline_profile() -> Profile:
    capture_path = Path("captures/experiments/read_01_initial_load.json")
    state = DeviceState.load_json(capture_path)
    return state.create_profile(1, "Baseline Profile")


class TestProfileRGBMethods:
    def test_get_rgb_editor(self, baseline_profile: Profile):
        ed = baseline_profile.get_rgb_editor()
        assert isinstance(ed, RGBGlobalEditor)
        assert ed.effect == EFFECT_RIPPLE_SPREAD
        assert ed.color_mode == 1
        assert ed.brightness == 5
        assert ed.speed == 5

    def test_set_rgb_global_from_editor(self, baseline_profile: Profile):
        ed = baseline_profile.get_rgb_editor()
        ed.set_effect(EFFECT_STATIC).set_single_color("#FF0000").set_speed(3)
        baseline_profile.set_rgb_global(ed)

        assert baseline_profile.rgb_global is not None
        assert baseline_profile.rgb_global.effect == EFFECT_STATIC
        assert baseline_profile.rgb_global.color_mode == 0
        assert baseline_profile.rgb_global.primary == (255, 0, 0)
        assert baseline_profile.rgb_global.speed == 3

    def test_set_single_color_ripple(self, baseline_profile: Profile):
        # The exact forensic requirement: explicitly setting orange reactive ripple
        baseline_profile.set_single_color_ripple(color="#FFA500", brightness=5, speed=5)

        rg = baseline_profile.rgb_global
        assert rg is not None
        assert rg.effect == EFFECT_RIPPLE_SPREAD
        assert rg.color_mode == 0  # Single Color!
        assert rg.primary == (255, 165, 0)
        assert rg.secondary == (0, 0, 0)
        assert rg.brightness == 5
        assert rg.speed == 5

    def test_set_rainbow_ripple(self, baseline_profile: Profile):
        baseline_profile.set_rainbow_ripple(brightness=4, speed=2)

        rg = baseline_profile.rgb_global
        assert rg is not None
        assert rg.effect == EFFECT_RIPPLE_SPREAD
        assert rg.color_mode == 1  # Rainbow!
        assert rg.primary == (255, 255, 255)
        assert rg.brightness == 4
        assert rg.speed == 2

    def test_json_roundtrip_preserves_rgb(self, baseline_profile: Profile):
        baseline_profile.set_single_color_ripple("#FF8000")
        json_str = baseline_profile.to_json()

        restored = Profile.from_json(json_str)
        assert restored.rgb_global is not None
        assert restored.rgb_global.effect == EFFECT_RIPPLE_SPREAD
        assert restored.rgb_global.color_mode == 0
        assert restored.rgb_global.primary == (255, 128, 0)
        assert restored.rgb_global.brightness == 5
        assert restored.rgb_global.speed == 5


class TestProfileManagerRGBIntegration:
    def test_update_profile_rgb(self, baseline_profile: Profile):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = ProfileManager(storage_dir=tmpdir)
            mgr.save_profile(baseline_profile)

            # Update via editor
            ed = RGBGlobalEditor().set_single_color_ripple("#00FF00")
            updated = mgr.update_profile_rgb(1, ed, save=True)

            assert updated.rgb_global is not None
            assert updated.rgb_global.primary == (0, 255, 0)
            assert updated.rgb_global.color_mode == 0

            # Reload from disk to verify persistence
            reloaded = mgr.load_profile(1)
            assert reloaded.rgb_global is not None
            assert reloaded.rgb_global.primary == (0, 255, 0)
            assert reloaded.rgb_global.color_mode == 0

    def test_diff_profiles_detects_rgb_changes(self, baseline_profile: Profile):
        mgr = ProfileManager()
        modified = baseline_profile.clone()
        # Change color_mode from 1 to 0 and primary to orange
        modified.set_single_color_ripple("#FF8000")

        diff = mgr.compare_profiles(baseline_profile, modified)
        sub_rgb = diff.get_subsystem("rgb_global")
        assert sub_rgb is not None
        assert sub_rgb.has_changes is True

        param_names = [d.parameter for d in sub_rgb.details]
        assert "primary_color" in param_names
        assert "color_mode" in param_names

    def test_build_write_plan_rgb_step(self, baseline_profile: Profile):
        capture_path = Path("captures/experiments/read_01_initial_load.json")
        state = DeviceState.load_json(capture_path)

        # Target profile with modified single-color ripple
        target = state.create_profile(1, "Baseline Profile")
        target.set_single_color_ripple("#FF8000")

        plan = build_profile_write_plan(state, target)
        assert len(plan.steps) == 1
        step = plan.steps[0]
        assert step.subsystem == "rgb_global"
        assert step.opcode == 0x23
        assert len(step.chunks) == 1

        # Check packet content
        pkt = step.chunks[0].packet
        assert pkt[0:3] == b"\xAA\x23\x10"
        assert pkt[8] == 0x0F  # Ripple
        assert pkt[9:12] == bytes([255, 128, 0])
        assert pkt[16] == 0x00  # Single Color!
