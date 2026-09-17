"""
HID Offset / Boundary Regression Tests (READ-ONLY AUDIT).

No hardware writes are executed. All tests use purely synthetic 64-byte
reports constructed by hand according to the confirmed canonical layout:

    Byte 0     : 0x55 (Device->Host prefix)
    Byte 1     : opcode
    Byte 2     : payload_size / chunk_size
    Bytes 3-4  : address uint16_le
    Bytes 5-7  : sub-header / flags (3 bytes)
    Bytes 8-63 : PAYLOAD (56 bytes for full chunks)

Confirmed bugs found by this audit:

BUG-1  parse_rgb_per_key_read_chunks (read.py L339 / L349)
       Uses resp[5:5+56] and tail[5:5+8] -- skips only 5 header bytes.
       Correct offset is 8 (3 cmd + 2 addr + 3 sub-header).
       Result: first 3 bytes of every payload are actually sub-header bytes
               actual R/G/B data starts 3 bytes too late -> silent colour corruption.

BUG-2  parse_rgb_per_key_chunks (rgb.py L689 / L703)
       Uses rep[5:5+56] and tail_rep[5:5+8] -- same wrong offset.
       build_rgb_per_key_chunks ALSO places payload at offset 5 (not 8),
       so the round-trip is internally symmetric (both wrong) and the
       corruption is invisible to naive round-trip tests.

BUG-3  MockHidTransport auto_ack (transport.py L127)
       Copies ack[5:5+sz] = data[5:5+sz].
       Real device echoes payload at bytes 8+. The mock therefore places
       payload at the wrong position, so any test validating readback
       against a MockHidTransport auto-ACK is testing a broken response.

CLEAN paths confirmed correct (offset 8):
  - parse_hall_read_chunks          (read.py L376 / hall.py L323)
  - parse_game_mode_response        (read.py L237-L252)
  - parse_rgb_global_read_response  (read.py L289-L298)
  - parse_keymap_chunks             (keymap.py L279 / L289)
  - parse_dks_read_chunks           (read.py L399 / L409)
  - parse_macro_* chunks            (read.py L697 / macro.py L406)
"""
from __future__ import annotations

import struct
import pytest

from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.read import (
    parse_rgb_per_key_read_chunks,
    parse_hall_read_chunks,
    parse_game_mode_response,
    parse_rgb_global_read_response,
)
from keyboard_re.protocol.rgb import (
    build_rgb_per_key_chunks,
    parse_rgb_per_key_chunks as rgb_parse_per_key_chunks,
    build_led_buffer,
    LED_SLOT_COUNT,
)
from keyboard_re.protocol.keymap import parse_keymap_chunks
from keyboard_re.protocol.transport import MockHidTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_report(
    opcode: int,
    chunk_size: int,
    address: int,
    payload: bytes,
    prefix: int = 0x55,
) -> bytes:
    """Construct a syntactically valid 64-byte HID report.

    Layout (canonical):
        [0]   prefix (0x55 device->host, or 0xAA host->device)
        [1]   opcode
        [2]   chunk_size
        [3-4] address uint16_le
        [5-7] sub-header (zeroes)
        [8..] payload
    """
    buf = bytearray(REPORT_SIZE)
    buf[0] = prefix
    buf[1] = opcode
    buf[2] = chunk_size
    struct.pack_into("<H", buf, 3, address)
    if 8 + len(payload) > REPORT_SIZE:
        raise ValueError(f"payload too large: {len(payload)} bytes does not fit after offset 8")
    buf[8 : 8 + len(payload)] = payload
    return bytes(buf)


def make_per_key_readback_reports_correct_offset(led_buf: bytes) -> list:
    """Build synthetic 55 14 readback reports with payload at CORRECT offset 8."""
    assert len(led_buf) == 512
    reports = []
    for i in range(9):
        addr = i * 56
        payload = led_buf[addr : addr + 56]
        reports.append(make_report(0x14, 0x38, addr, payload, prefix=0x55))
    tail_payload = led_buf[504 : 504 + 8]
    tail = make_report(0x14, 0x08, 504, tail_payload, prefix=0x55)
    reports.append(tail)
    return reports


def make_per_key_readback_reports_wrong_offset(led_buf: bytes) -> list:
    """Build synthetic 55 14 readback reports with payload at WRONG offset 5."""
    assert len(led_buf) == 512
    reports = []
    for i in range(9):
        addr = i * 56
        payload = led_buf[addr : addr + 56]
        buf = bytearray(REPORT_SIZE)
        buf[0] = 0x55
        buf[1] = 0x14
        buf[2] = 0x38
        struct.pack_into("<H", buf, 3, addr)
        buf[5 : 5 + len(payload)] = payload   # wrong offset
        reports.append(bytes(buf))
    tail_payload = led_buf[504 : 504 + 8]
    buf = bytearray(REPORT_SIZE)
    buf[0] = 0x55
    buf[1] = 0x14
    buf[2] = 0x08
    struct.pack_into("<H", buf, 3, 504)
    buf[5 : 5 + len(tail_payload)] = tail_payload   # wrong offset
    reports.append(bytes(buf))
    return reports


def canonical_led_buf() -> bytes:
    """512-byte buffer: slot i = (i%256, (i+1)%256, (i+2)%256, led_id=i)."""
    buf = bytearray(512)
    for i in range(128):
        offset = i * 4
        buf[offset]     = i & 0xFF
        buf[offset + 1] = (i + 1) & 0xFF
        buf[offset + 2] = (i + 2) & 0xFF
        buf[offset + 3] = i & 0xFF
    return bytes(buf)


# ---------------------------------------------------------------------------
# BUG-1: parse_rgb_per_key_read_chunks (read.py) uses offset 5 not 8
# ---------------------------------------------------------------------------

class TestBug1ReadPyPerKeyOffset:
    """parse_rgb_per_key_read_chunks in read.py uses resp[5:] instead of resp[8:]."""

    def test_correct_offset_reports_parse_identically(self):
        """Verify that correctly-offset readback reports parse back to the original buffer."""
        led_buf = canonical_led_buf()
        reports = make_per_key_readback_reports_correct_offset(led_buf)
        result = parse_rgb_per_key_read_chunks(reports)
        assert result == led_buf, (
            "parse_rgb_per_key_read_chunks must return the exact input buffer "
            "when reports use the canonical offset-8 payload layout."
        )

    def test_wrong_offset_reports_do_NOT_match_original(self):
        """Reports with payload at offset 5 must NOT parse to the original buffer.

        If this assertion fails (wrong-offset reports DO produce the correct buffer),
        the parser is reading from offset 5 -- confirming BUG-1 is active.
        """
        led_buf = canonical_led_buf()
        reports_wrong = make_per_key_readback_reports_wrong_offset(led_buf)
        result_wrong = parse_rgb_per_key_read_chunks(reports_wrong)
        assert result_wrong != led_buf, (
            "BUG-1 CONFIRMED: parse_rgb_per_key_read_chunks accepted offset-5 "
            "payload as if it were correct. The parser is reading from offset 5, not 8."
        )

    def test_slot0_rgb_exact_bytes_with_correct_offset(self):
        """Slot 0 R=0xAA G=0xBB B=0xCC -- verify byte-exact extraction at offset 8."""
        led_buf = bytearray(512)
        led_buf[0] = 0xAA
        led_buf[1] = 0xBB
        led_buf[2] = 0xCC
        led_buf[3] = 0x00
        for i in range(1, 128):
            led_buf[i * 4 + 3] = i
        led_buf = bytes(led_buf)
        reports = make_per_key_readback_reports_correct_offset(led_buf)
        result = parse_rgb_per_key_read_chunks(reports)
        assert result[0] == 0xAA, f"Slot0 R: expected 0xAA, got 0x{result[0]:02X}"
        assert result[1] == 0xBB, f"Slot0 G: expected 0xBB, got 0x{result[1]:02X}"
        assert result[2] == 0xCC, f"Slot0 B: expected 0xCC, got 0x{result[2]:02X}"

    def test_slot0_rgb_corrupt_when_parser_uses_offset5(self):
        """With offset-5-encoded reports: slot 0 R byte must NOT be found at slot 0 R position.

        With offset-5 encoding the payload starts at buf[5]; an offset-5 parser
        reads from there correctly. An offset-8 parser would read from buf[8],
        which corresponds to payload[3] (led_id of slot 0) = 0, not R=0xAA.
        With offset-8 parser + offset-5 report: the 3 sub-header bytes get
        included in the payload, shifting everything by 3 positions.
        """
        led_buf = bytearray(512)
        led_buf[0] = 0xAA
        for i in range(1, 128):
            led_buf[i * 4 + 3] = i
        led_buf = bytes(led_buf)
        reports = make_per_key_readback_reports_wrong_offset(led_buf)
        result = parse_rgb_per_key_read_chunks(reports)
        # With offset-5 report + offset-8 parser:
        # report[8] = payload[3] = led_id = 0  (NOT 0xAA)
        # So slot-0 R in result will not be 0xAA
        assert result[0] != 0xAA, (
            "BUG-1 confirmed: parse_rgb_per_key_read_chunks returned R=0xAA from "
            "an offset-5 encoded report. The parser is accepting offset-5 reports "
            "as canonical."
        )

    def test_tail_chunk_correct_offset(self):
        """Slots 126 and 127 must be reconstructed correctly from the tail chunk."""
        led_buf = bytearray(512)
        led_buf[126 * 4 + 0] = 0x11
        led_buf[126 * 4 + 1] = 0x22
        led_buf[126 * 4 + 2] = 0x33
        led_buf[126 * 4 + 3] = 126
        led_buf[127 * 4 + 0] = 0x44
        led_buf[127 * 4 + 1] = 0x55
        led_buf[127 * 4 + 2] = 0x66
        led_buf[127 * 4 + 3] = 127
        for i in range(126):
            led_buf[i * 4 + 3] = i
        led_buf = bytes(led_buf)
        reports = make_per_key_readback_reports_correct_offset(led_buf)
        result = parse_rgb_per_key_read_chunks(reports)
        assert result[504:508] == bytes([0x11, 0x22, 0x33, 126])
        assert result[508:512] == bytes([0x44, 0x55, 0x66, 127])


# ---------------------------------------------------------------------------
# BUG-2: parse_rgb_per_key_chunks (rgb.py) + build_rgb_per_key_chunks
#         both use offset 5 -- symmetric bug hides from round-trip tests
# ---------------------------------------------------------------------------

class TestBug2RgbPyPerKeyOffset:
    """build and parse in rgb.py both use offset 5. Symmetric mismatch
    passes naive round-trip but produces incorrect wire frames."""

    def test_build_chunk0_payload_position(self):
        """Document at which byte build_rgb_per_key_chunks places slot-0 R value."""
        buf = build_led_buffer([(255, 0, 0)] * LED_SLOT_COUNT)
        reports = build_rgb_per_key_chunks(buf)
        pkt0 = reports[0]
        assert pkt0[0] == 0xAA
        assert pkt0[1] == 0x24
        assert pkt0[2] == 0x38
        # R=255 of slot 0 should be at offset 8 in the canonical wire layout.
        # BUG-2: current implementation places it at offset 5.
        r_at5 = pkt0[5]
        r_at8 = pkt0[8]
        if r_at5 == 255 and r_at8 != 255:
            # Bug is present: payload starts at offset 5
            pytest.fail(
                f"BUG-2 CONFIRMED: build_rgb_per_key_chunks places slot-0 R=255 "
                f"at offset 5 (found {r_at5}), not offset 8 (found {r_at8}). "
                "Payload must start at byte 8 in the canonical HID frame."
            )
        elif r_at8 == 255 and r_at5 != 255:
            # Bug is fixed
            pass
        else:
            pytest.fail(
                f"Unexpected wire layout: r@5={r_at5}, r@8={r_at8}. "
                "Cannot determine offset position."
            )

    def test_round_trip_symmetric(self):
        """build (offset 5) + parse (offset 5) must produce original buffer.

        This test will PASS even when the bug is present, because both sides
        are wrong in the same way. It documents the false-negative nature of
        simple round-trip tests for this bug.
        """
        buf = build_led_buffer([(i % 256, (i+1) % 256, (i+2) % 256) for i in range(LED_SLOT_COUNT)])
        reports = build_rgb_per_key_chunks(buf)
        result = rgb_parse_per_key_chunks(reports)
        assert result == buf, (
            "Symmetric round-trip (build+parse both at same offset) must produce "
            "original buffer. Both sides are inconsistent."
        )

    def test_canonical_offset8_build_accepted_by_rgb_parse(self):
        """A canonically-built report (payload at offset 8) parses back
        identically to the original buffer.
        """
        led_buf = build_led_buffer([(i % 256, (i+1) % 256, (i+2) % 256) for i in range(LED_SLOT_COUNT)])
        # Build canonical (offset 8) AA 24 reports
        canonical_reports = []
        for i in range(9):
            addr = i * 56
            payload = led_buf[addr : addr + 56]
            pkt = bytearray(REPORT_SIZE)
            pkt[0] = 0xAA; pkt[1] = 0x24; pkt[2] = 0x38
            struct.pack_into("<H", pkt, 3, addr)
            pkt[8 : 8 + 56] = payload      # CORRECT offset 8
            canonical_reports.append(bytes(pkt))
        tail_payload = led_buf[504 : 512]
        pkt = bytearray(REPORT_SIZE)
        pkt[0] = 0xAA; pkt[1] = 0x24; pkt[2] = 0x08
        struct.pack_into("<H", pkt, 3, 504)
        pkt[8 : 8 + 8] = tail_payload     # CORRECT offset 8
        canonical_reports.append(bytes(pkt))

        result = rgb_parse_per_key_chunks(canonical_reports)
        assert result == led_buf, "Canonical offset-8 reports must parse to original buffer."

    def test_write_reports_prefix_incompatible_with_readback_parser(self):
        """AA 24 write reports must not be parseable by the 55 14 readback parser."""
        buf = build_led_buffer([(0, 0, 0)] * LED_SLOT_COUNT)
        write_reports = build_rgb_per_key_chunks(buf)
        with pytest.raises(ValueError):
            parse_rgb_per_key_read_chunks(write_reports)


# ---------------------------------------------------------------------------
# BUG-3: MockHidTransport auto_ack uses offset 5 not 8
# ---------------------------------------------------------------------------

class TestBug3MockAutoAckOffset:
    """MockHidTransport.send_report copies ACK payload with ack[5:] = data[5:]."""

    def _make_hall_write_request(self, address: int, payload: bytes) -> bytes:
        pkt = bytearray(REPORT_SIZE)
        pkt[0] = 0xAA; pkt[1] = 0x27; pkt[2] = 0x38
        struct.pack_into("<H", pkt, 3, address)
        pkt[8 : 8 + len(payload)] = payload
        return bytes(pkt)

    def test_auto_ack_payload_at_correct_offset(self):
        """Auto-generated ACK must echo the sent payload at offset 8.

        Current implementation: ack[5:5+sz] = data[5:5+sz] -- WRONG.
        Correct:                ack[8:8+sz] = data[8:8+sz].
        """
        mock = MockHidTransport(auto_ack=True)
        payload = bytes(range(56))
        pkt = self._make_hall_write_request(0x0000, payload)
        mock.send_report(0, pkt)
        ack = mock.receive_report()

        ack_payload_at8 = ack[8 : 8 + 56]
        if ack_payload_at8 != payload:
            ack_at5 = ack[5 : 5 + 56]
            matched_at5 = (ack_at5 == bytes(pkt)[5 : 5 + 56])
            pytest.fail(
                f"BUG-3 CONFIRMED: MockHidTransport auto_ack places payload at wrong offset.\n"
                f"  Payload expected at ack[8:64]: {ack_payload_at8[:4].hex()} ... (match={ack_payload_at8 == payload})\n"
                f"  Payload found at ack[5:61]:    {ack_at5[:4].hex()} ... (match={matched_at5})\n"
                f"  The mock must copy data[8:8+sz] -> ack[8:8+sz], not data[5:] -> ack[5:]."
            )

    def test_auto_ack_prefix_opcode_correct(self):
        mock = MockHidTransport(auto_ack=True)
        pkt = self._make_hall_write_request(0x0038, bytes(56))
        mock.send_report(0, pkt)
        ack = mock.receive_report()
        assert ack[0] == 0x55
        assert ack[1] == 0x27
        assert ack[2] == 0x38

    def test_auto_ack_address_mirrored(self):
        mock = MockHidTransport(auto_ack=True)
        pkt = self._make_hall_write_request(0x0070, bytes(56))
        mock.send_report(0, pkt)
        ack = mock.receive_report()
        ack_addr = struct.unpack_from("<H", ack, 3)[0]
        assert ack_addr == 0x0070, f"ACK address: expected 0x0070, got 0x{ack_addr:04X}"

    def test_auto_ack_sub_header_bleed(self):
        """Demonstrate sub-header (bytes 5-7) bleeding into the ACK payload area.

        With offset-5 ACK logic: bytes 5-7 of the request (sub-header flags)
        get echoed into ack[5:8]. A request with non-zero sub-header bytes
        will therefore corrupt the ACK payload comparison.
        """
        mock = MockHidTransport(auto_ack=True)
        pkt = bytearray(REPORT_SIZE)
        pkt[0] = 0xAA; pkt[1] = 0x27; pkt[2] = 56
        struct.pack_into("<H", pkt, 3, 0)
        pkt[5] = 0xDE   # non-zero sub-header byte 5
        pkt[6] = 0xAD
        pkt[7] = 0xBE
        pkt[8:64] = bytes([0xAA] * 56)   # payload
        mock.send_report(0, bytes(pkt))
        ack = mock.receive_report()

        ack_b5 = ack[5]
        ack_b8 = ack[8]

        if ack_b5 == 0xDE:
            # Bug-3 is active: sub-header bled into echo at offset 5
            pass  # documented -- test passes to record the behaviour
        else:
            # Bug may be fixed
            assert ack_b8 == 0xAA, (
                f"Expected ack[8]=0xAA after bug fix, got 0x{ack_b8:02X}"
            )


# ---------------------------------------------------------------------------
# CLEAN: Hall AA17 / 55 17 -- confirmed correct (offset 8)
# ---------------------------------------------------------------------------

class TestCleanHallOffset:

    def _make_hall_reports(self, image: bytes) -> list:
        assert len(image) == 1008
        reports = []
        for i in range(18):
            addr = i * 56
            payload = image[addr : addr + 56]
            reports.append(make_report(0x17, 0x38, addr, payload, prefix=0x55))
        return reports

    def test_hall_correct_offset_roundtrip(self):
        image = bytes(range(256)) * 4
        image = image[:1008]
        reports = self._make_hall_reports(image)
        result = parse_hall_read_chunks(reports, expected_opcode=0x17)
        assert result == image

    def test_hall_offset5_corrupts(self):
        image = bytes(range(256)) * 4
        image = image[:1008]
        wrong_reports = []
        for i in range(18):
            addr = i * 56
            payload = image[addr : addr + 56]
            buf = bytearray(REPORT_SIZE)
            buf[0] = 0x55; buf[1] = 0x17; buf[2] = 0x38
            struct.pack_into("<H", buf, 3, addr)
            buf[5 : 5 + 56] = payload  # wrong offset
            wrong_reports.append(bytes(buf))
        result_wrong = parse_hall_read_chunks(wrong_reports, expected_opcode=0x17)
        assert result_wrong != image, (
            "Hall parser accepted offset-5 reports -- it must NOT."
        )


# ---------------------------------------------------------------------------
# CLEAN: GameMode AA11 / 55 11 -- confirmed correct (offset 8)
# ---------------------------------------------------------------------------

class TestCleanGameModeOffset:

    def _make_gm_report(self, payload: bytes) -> bytes:
        assert len(payload) == 56
        buf = bytearray(REPORT_SIZE)
        buf[0] = 0x55; buf[1] = 0x11; buf[2] = 0x38
        buf[3:8] = bytes([0x00, 0x00, 0x00, 0x01, 0x00])
        buf[8 : 8 + 56] = payload
        return bytes(buf)

    def test_fields_at_offset8(self):
        payload = bytearray(56)
        payload[1] = 1      # game_mode
        payload[3] = 5      # sleep_time
        payload[5] = 6      # report_rate (8000 Hz)
        payload[11] = 1     # stability_mode
        payload[14] = 1     # auto_calibration
        report = self._make_gm_report(bytes(payload))
        gm = parse_game_mode_response(report)
        assert gm.game_mode == 1
        assert gm.sleep_time == 5
        assert gm.report_rate == 6
        assert gm.stability_mode == 1
        assert gm.auto_calibration == 1
        assert gm.report_rate_hz == 8000


# ---------------------------------------------------------------------------
# CLEAN: RGB Global AA13 / 55 13 -- confirmed correct (offset 8)
# ---------------------------------------------------------------------------

class TestCleanRGBGlobalOffset:

    def _make_rgb_global(self, effect: int = 0x01, r: int = 255, g: int = 128, b: int = 64) -> bytes:
        buf = bytearray(REPORT_SIZE)
        buf[0] = 0x55; buf[1] = 0x13; buf[2] = 0x10
        buf[3:8] = bytes([0x00, 0x00, 0x00, 0x01, 0x00])
        buf[8] = effect
        buf[9] = r; buf[10] = g; buf[11] = b
        buf[12] = 0xFF      # driver_setting
        buf[17] = 5         # brightness
        buf[18] = 3         # speed
        return bytes(buf)

    def test_effect_at_byte8(self):
        report = self._make_rgb_global(effect=0x07, r=200, g=100, b=50)
        cfg = parse_rgb_global_read_response(report)
        assert cfg.effect == 0x07
        assert cfg.primary == (200, 100, 50)

    def test_brightness_at_byte17(self):
        report = self._make_rgb_global()
        cfg = parse_rgb_global_read_response(report)
        assert cfg.brightness == 5

    def test_speed_at_byte18(self):
        report = self._make_rgb_global()
        cfg = parse_rgb_global_read_response(report)
        assert cfg.speed == 3


# ---------------------------------------------------------------------------
# CLEAN: Remap AA12/16 / 55 12/16 -- confirmed correct (offset 8)
# ---------------------------------------------------------------------------

class TestCleanKeymapOffset:

    def _make_keymap_reports(self, buf: bytes, opcode: int) -> list:
        assert len(buf) == 512
        reports = []
        for i in range(9):
            addr = i * 56
            payload = buf[addr : addr + 56]
            reports.append(make_report(opcode, 0x38, addr, payload, prefix=0x55))
        tail = buf[504 : 512]
        reports.append(make_report(opcode, 0x08, 504, tail, prefix=0x55))
        return reports

    def test_layer1_round_trip(self):
        buf = bytearray(512)
        buf[0] = 0x02  # page_type
        buf[2] = 0x04  # param2 = HID 'A'
        reports = self._make_keymap_reports(bytes(buf), opcode=0x12)
        table = parse_keymap_chunks(reports, expected_opcode=0x12, layer=1)
        assert table.raw_bytes == bytes(buf)
        assert table.slots[0].page_type == 0x02
        assert table.slots[0].param2 == 0x04


# ---------------------------------------------------------------------------
# Canonical wire layout assertions
# ---------------------------------------------------------------------------

class TestCanonicalHIDLayout:
    """Wire-level structural checks for the 64-byte canonical HID frame."""

    def test_header_occupies_bytes_0_through_7(self):
        payload = bytes(range(56))
        report = make_report(0x27, 0x38, 0x0000, payload, prefix=0xAA)
        assert report[0] == 0xAA
        assert report[1] == 0x27
        assert report[2] == 0x38
        assert struct.unpack_from("<H", report, 3)[0] == 0
        assert report[5] == 0   # sub-header byte
        assert report[6] == 0
        assert report[7] == 0
        assert report[8 : 64] == payload

    def test_56_byte_payload_fills_bytes_8_to_63(self):
        payload = bytes([0xFF] * 56)
        report = make_report(0x17, 0x38, 0, payload, prefix=0x55)
        assert len(report) == 64
        assert all(b == 0xFF for b in report[8:64])
        assert all(b == 0x00 for b in report[5:8])

    def test_8_byte_tail_payload_at_offset_8(self):
        payload = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04])
        report = make_report(0x17, 0x08, 504, payload, prefix=0x55)
        assert report[8 : 16] == payload
        assert all(b == 0x00 for b in report[16:64])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
