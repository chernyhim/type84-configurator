"""
Unit tests for RGBMatrix model, Per-Key slot manipulation, validation,
physical key mapping, AA24 chunking, and Profile integration.

Pure offline tests: zero physical hardware interaction.
"""

from pathlib import Path
import tempfile
import pytest

from keyboard_re.models.rgb_matrix import RGBMatrix
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    LED_BUFFER_SIZE,
    LED_SLOT_COUNT,
    LED_SLOT_SIZE,
    RGB_PER_KEY_CHUNK_COUNT,
    RGB_PER_KEY_CHUNK_PAYLOAD_SIZE,
    RGB_PER_KEY_TAIL_ADDR,
    RGB_PER_KEY_TOTAL_REPORTS,
    build_rgb_per_key_chunks,
    parse_rgb_per_key_chunks,
)


class TestRGBMatrixModel:
    def test_default_creation(self):
        mat = RGBMatrix()
        assert len(mat) == LED_SLOT_COUNT
        raw = mat.to_raw()
        assert len(raw) == LED_BUFFER_SIZE

        # Verify all slots default to RGB (0,0,0) and LED_ID == slot
        for i in range(LED_SLOT_COUNT):
            assert mat.get_led(i) == (0, 0, 0)
            assert mat.get_led_id(i) == i

    def test_from_raw_roundtrip(self):
        mat = RGBMatrix()
        mat.set_led(0, (255, 0, 0))
        mat.set_led(35, (0, 255, 0))
        mat.set_led(127, (0, 0, 255))

        raw = mat.to_raw()
        restored = RGBMatrix.from_raw(raw)
        assert restored == mat
        assert restored.get_led(0) == (255, 0, 0)
        assert restored.get_led(35) == (0, 255, 0)
        assert restored.get_led(127) == (0, 0, 255)
        assert restored.to_raw() == raw

    def test_invalid_buffer_length(self):
        with pytest.raises(ValueError, match="must be exactly 512 bytes"):
            RGBMatrix(b"\x00" * 511)
        with pytest.raises(ValueError, match="must be exactly 512 bytes"):
            RGBMatrix(b"\x00" * 513)

    def test_set_led_and_indexing(self):
        mat = RGBMatrix()
        mat.set_led(10, "#FF8000")
        assert mat.get_led(10) == (255, 128, 0)
        assert mat[10] == (255, 128, 0)
        assert mat.get_led_id(10) == 10  # Preserves LED_ID

        mat[10] = (10, 20, 30)
        assert mat.get_led(10) == (10, 20, 30)

    def test_set_led_invalid_slot(self):
        mat = RGBMatrix()
        with pytest.raises(IndexError):
            mat.set_led(-1, (255, 0, 0))
        with pytest.raises(IndexError):
            mat.set_led(128, (255, 0, 0))

    def test_set_led_invalid_rgb(self):
        mat = RGBMatrix()
        with pytest.raises(ValueError):
            mat.set_led(0, (256, 0, 0))
        with pytest.raises(ValueError):
            mat.set_led(0, (-1, 0, 0))
        with pytest.raises(ValueError):
            mat.set_led(0, "#GGGGGG")

    def test_set_many(self):
        mat = RGBMatrix()
        mat.set_many({
            1: (255, 0, 0),
            2: "#00FF00",
            3: (0, 0, 255),
        })
        assert mat.get_led(1) == (255, 0, 0)
        assert mat.get_led(2) == (0, 255, 0)
        assert mat.get_led(3) == (0, 0, 255)

    def test_fill_and_clear(self):
        mat = RGBMatrix()
        mat.fill("#0000FF")
        for i in range(LED_SLOT_COUNT):
            assert mat.get_led(i) == (0, 0, 255)
            assert mat.get_led_id(i) == i  # Preserved LED_ID

        mat.clear()
        for i in range(LED_SLOT_COUNT):
            assert mat.get_led(i) == (0, 0, 0)
            assert mat.get_led_id(i) == i

    def test_physical_key_led_mapping(self):
        mat = RGBMatrix()
        # Key 'W' is known in PHYSICAL_LED_MAP (from hardware capture W is at slot 35)
        # Let's test setting key 'ESC'
        mat.set_key_led("ESC", "#FF0000")
        assert mat.get_key_led("ESC") == (255, 0, 0)

        # Unconfirmed key name raises KeyError
        with pytest.raises(KeyError, match="not confirmed"):
            mat.set_key_led("UNKNOWN_KEY_NAME_XYZ", "#FF0000")

    def test_diff_matrix(self):
        mat_a = RGBMatrix()
        mat_b = RGBMatrix()

        mat_b.set_led(5, "#FF0000")
        mat_b.set_led(50, "#00FF00")

        d = mat_a.diff(mat_b)
        assert len(d) == 2
        assert d[5] == ((0, 0, 0), (255, 0, 0))
        assert d[50] == ((0, 0, 0), (0, 255, 0))

    def test_preserve_unknown_led_id(self):
        # Construct raw buffer with custom LED_ID bytes (e.g. 0xFE, 0xFD)
        raw = bytearray(LED_BUFFER_SIZE)
        raw[0:4] = bytes([10, 20, 30, 0xFE])
        raw[4:8] = bytes([40, 50, 60, 0xFD])
        for i in range(2, LED_SLOT_COUNT):
            raw[i * 4 + 3] = i

        mat = RGBMatrix.from_raw(raw)
        assert mat.get_led_id(0) == 0xFE
        assert mat.get_led_id(1) == 0xFD

        # Modifying color of slot 0 MUST preserve LED_ID 0xFE
        mat.set_led(0, "#FFFFFF")
        assert mat.get_led(0) == (255, 255, 255)
        assert mat.get_led_id(0) == 0xFE
        assert mat.to_raw()[3] == 0xFE


class TestRGBMatrixChunking:
    def test_aa24_exact_10_chunks(self):
        mat = RGBMatrix()
        mat.set_led(35, (0, 255, 0))  # W key to green matching rgb_05_per_key capture

        chunks = mat.to_chunks()
        assert len(chunks) == RGB_PER_KEY_TOTAL_REPORTS  # Exactly 10 reports

        # First 9 chunks: 56 bytes payload each
        for i in range(9):
            pkt = chunks[i]
            assert len(pkt) == 64
            assert pkt[0:3] == b"\xAA\x24\x38"
            expected_addr = i * 56
            addr_in_pkt = pkt[3] | (pkt[4] << 8)
            assert addr_in_pkt == expected_addr

        # 10th chunk: 8 bytes tail at 0x01F8 (504)
        tail_pkt = chunks[9]
        assert len(tail_pkt) == 64
        assert tail_pkt[0:3] == b"\xAA\x24\x08"
        tail_addr = tail_pkt[3] | (tail_pkt[4] << 8)
        assert tail_addr == RGB_PER_KEY_TAIL_ADDR  # 504 (0x01F8)

        # Reconstruct buffer from chunks
        reconstructed = parse_rgb_per_key_chunks(chunks)
        assert reconstructed == mat.to_raw()


class TestProfileCustomPerKeyIntegration:
    @pytest.fixture
    def baseline_state(self) -> DeviceState:
        capture_path = Path("captures/experiments/read_01_initial_load.json")
        return DeviceState.load_json(capture_path)

    def test_set_custom_per_key_profile(self, baseline_state: DeviceState):
        prof = baseline_state.create_profile(1, "Custom Profile")
        matrix = RGBMatrix()
        matrix.set_led(35, "#00FF00")  # Key W to green

        prof.set_custom_per_key_profile(matrix=matrix, brightness=4)

        assert prof.rgb_global is not None
        assert prof.rgb_global.effect == EFFECT_CUSTOM  # 0x80
        assert prof.rgb_global.brightness == 4
        assert prof.rgb_matrix is not None
        assert len(prof.rgb_matrix) == 512

        mat_view = prof.get_rgb_matrix()
        assert mat_view.get_led(35) == (0, 255, 0)

    def test_custom_profile_generates_aa23_and_10_aa24_steps(self, baseline_state: DeviceState):
        # Baseline state has default effect (0x0F)
        target = baseline_state.create_profile(1, "Custom Target")
        matrix = RGBMatrix()
        matrix.set_led(35, "#00FF00")
        target.set_custom_per_key_profile(matrix=matrix)

        plan = build_profile_write_plan(baseline_state, target)

        # Plan must coordinate:
        # Step for rgb_global (AA 23, 1 packet)
        # Step for rgb_matrix (AA 24, 10 chunks)
        step_g = plan.get_step("rgb_global")
        step_m = plan.get_step("rgb_matrix")

        assert step_g is not None
        assert step_g.opcode == 0x23
        assert step_g.packet_count == 1
        assert step_g.chunks[0].packet[8] == EFFECT_CUSTOM  # 0x80

        assert step_m is not None
        assert step_m.opcode == 0x24
        assert step_m.packet_count == 10  # 10 chunks!

    def test_profile_manager_update_rgb_matrix(self, baseline_state: DeviceState):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = ProfileManager(storage_dir=tmpdir)
            prof = baseline_state.create_profile(1, "P1")
            mgr.save_profile(prof)

            mat = RGBMatrix()
            mat.set_led(10, "#FF00FF")
            updated = mgr.update_profile_rgb_matrix(1, mat, save=True)

            assert updated.rgb_matrix == mat.to_raw()

            # Reload to verify persistence
            reloaded = mgr.load_profile(1)
            assert reloaded.rgb_matrix == mat.to_raw()
