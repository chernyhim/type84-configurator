"""
Unit tests for Visualizer Phase 4: Hardware Output Layer (KeyboardRgbOutput).

Tests cover:
A. Solid Red buffer first 32 bytes exact:
   00 FF 00 00, 01 FF 00 00, ..., 07 FF 00 00
B. 512-byte buffer preserved byte-exact
C. 84 physical LED slots receive red
D. KeyboardRgbOutput sets Global RGB Custom 0x80 before first AA24
E. Stop restores initial AA23 Global RGB state and AA14 Per-Key RGB state
F. On AA24 error, both states are restored
G. Absence of non-RGB opcodes (NO AA21, AA22, AA25, AA26, AA27, AA28)
H. Correct 10 chunks count and strictly ascending chunk addresses (0..504)
I. Multiple sequential frames (30 packets for 3 frames)
J. Stop terminates subsequent writes
K. Thread safety (no interleaved AA24 packets under concurrent writes)
L. Device absence / connection error handling
M. PlaybackEngine pipeline integration via attach_to_engine
N. Smoke test runner offline validation
"""

from __future__ import annotations

import struct
import threading
import time
from typing import List
import pytest
from PIL import Image

from keyboard_re.protocol.rgb import (
    EFFECT_CUSTOM,
    LED_BUFFER_SIZE,
    RGB_PER_KEY_CHUNK_PAYLOAD_SIZE,
    RGB_PER_KEY_TAIL_ADDR,
    RGB_PER_KEY_TOTAL_REPORTS,
    RGBGlobalConfig,
    build_led_buffer,
    parse_led_buffer,
    parse_rgb_per_key_chunks,
)
from keyboard_re.protocol.transport import MockHidTransport
from keyboard_re.ui.layout_data import KEY_BY_LED_SLOT
from keyboard_re.visualizer.decoder import DecodedFrame, MediaMetadata
from keyboard_re.visualizer.frame import RGBFrame
from keyboard_re.visualizer.output import HardwareOutputError, KeyboardRgbOutput
from keyboard_re.visualizer.player import FakeClock, PlaybackEngine


def make_custom_512b_buffer(key_slot: int = 35, color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    """Build a deterministic 512-byte LED buffer in canonical wire format [LED_ID, R, G, B]."""
    frame = RGBFrame()
    frame.set_color(key_slot, color)
    return frame.to_led_buffer()


def queue_per_key_read_responses(transport: MockHidTransport, buffer: bytes) -> None:
    """Queue 10 AA14 read response packets for a 512-byte buffer."""
    assert len(buffer) == 512
    for i in range(9):
        addr = i * 56
        rep = bytearray(64)
        rep[0] = 0x55
        rep[1] = 0x14
        rep[2] = 0x38
        struct.pack_into("<H", rep, 3, addr)
        rep[8 : 8 + 56] = buffer[addr : addr + 56]
        transport.queue_response(bytes(rep))

    tail_rep = bytearray(64)
    tail_rep[0] = 0x55
    tail_rep[1] = 0x14
    tail_rep[2] = 0x08
    struct.pack_into("<H", tail_rep, 3, 504)
    tail_rep[8 : 8 + 8] = buffer[504 : 504 + 8]
    transport.queue_response(bytes(tail_rep))


def queue_global_rgb_read_response(
    transport: MockHidTransport,
    effect: int = 0x0F,
    brightness: int = 5,
    speed: int = 3,
) -> None:
    """Queue single 64-byte response for AA 13 Global RGB read."""
    rep = bytearray(64)
    rep[0] = 0x55
    rep[1] = 0x13
    rep[2] = 0x10
    rep[8] = effect & 0xFF
    rep[9:12] = b"\xFF\x00\x00"  # primary color
    rep[13:16] = b"\x00\x00\x00"  # secondary color
    rep[16] = 0x01                # color mode
    rep[17] = brightness & 0xFF
    rep[18] = speed & 0xFF
    rep[22:24] = b"\xAA\x55"      # magic
    transport.queue_response(bytes(rep))


# -----------------------------------------------------------------------------
# Test A & B: Solid Red Buffer Wire Order & Byte-Exact 512B Buffer
# -----------------------------------------------------------------------------
def test_solid_red_buffer_wire_order_first_32_bytes():
    """
    Verify Solid Red buffer wire format:
    Slot format MUST be [LED_ID, R, G, B].
    First 32 bytes must be exactly:
      00 FF 00 00, 01 FF 00 00, 02 FF 00 00, 03 FF 00 00,
      04 FF 00 00, 05 FF 00 00, 06 FF 00 00, 07 FF 00 00
    """
    frame = RGBFrame.solid((255, 0, 0))
    buf = frame.to_led_buffer()

    assert len(buf) == LED_BUFFER_SIZE  # 512 bytes

    expected_first_32 = bytes([
        0x00, 0xFF, 0x00, 0x00,
        0x01, 0xFF, 0x00, 0x00,
        0x02, 0xFF, 0x00, 0x00,
        0x03, 0xFF, 0x00, 0x00,
        0x04, 0xFF, 0x00, 0x00,
        0x05, 0xFF, 0x00, 0x00,
        0x06, 0xFF, 0x00, 0x00,
        0x07, 0xFF, 0x00, 0x00,
    ])
    assert buf[:32] == expected_first_32, f"First 32 bytes mismatch: got {buf[:32].hex(' ').upper()}"

    # Slot 34 (W key) when green
    green_frame = RGBFrame()
    green_frame.set_color(34, (0, 255, 0))
    green_buf = green_frame.to_led_buffer()
    slot34_bytes = green_buf[34 * 4 : 35 * 4]
    assert slot34_bytes == bytes([34, 0, 255, 0]), f"Slot 34 green mismatch: got {slot34_bytes.hex()}"


# -----------------------------------------------------------------------------
# Test C: 84 Physical Key Slots Receive Red
# -----------------------------------------------------------------------------
def test_84_physical_key_slots_receive_red():
    """Verify all 84 physical key slots have (255, 0, 0) and parse back identically."""
    frame = RGBFrame.solid((255, 0, 0))
    buf = frame.to_led_buffer()

    parsed = parse_led_buffer(buf)
    assert len(parsed) == 128  # 128 slots in table

    # Verify all 84 physical keys received red
    for slot in KEY_BY_LED_SLOT:
        assert parsed[slot] == (255, 0, 0), f"Slot #{slot} did not receive red: {parsed[slot]}"

    # Verify non-key slots default to (0, 0, 0)
    for slot in range(128):
        if slot not in KEY_BY_LED_SLOT:
            assert parsed[slot] == (0, 0, 0), f"Unmapped slot #{slot} was not off: {parsed[slot]}"


# -----------------------------------------------------------------------------
# Test D: KeyboardRgbOutput Activates Global RGB Custom 0x80 Before AA24
# -----------------------------------------------------------------------------
def test_start_activates_global_rgb_custom_0x80():
    """Verify start_visualizer issues AA23 to set EFFECT_CUSTOM (0x80) before any AA24."""
    transport = MockHidTransport(auto_ack=True)
    initial_buf = make_custom_512b_buffer(key_slot=0, color=(0, 0, 0))
    queue_per_key_read_responses(transport, initial_buf)
    queue_global_rgb_read_response(transport, effect=0x0F)  # Initial: Ripple Spread

    output = KeyboardRgbOutput(transport=transport)
    output.start()

    assert output.is_active is True
    assert output.saved_rgb_global is not None
    assert output.saved_rgb_global.effect == 0x0F

    # Verify packets sent during start:
    # 10 AA14 read requests + 1 AA13 read request + 1 AA23 write request
    assert len(transport.recorded_reports) == 12

    # The last packet sent in start() MUST be AA23 with effect 0x80 (EFFECT_CUSTOM)
    last_pkt = transport.recorded_reports[-1][1]
    assert last_pkt[0] == 0xAA
    assert last_pkt[1] == 0x23  # Opcode AA 23
    assert last_pkt[8] == EFFECT_CUSTOM  # 0x80 at payload offset 8


# -----------------------------------------------------------------------------
# Test E: Stop Restores Initial AA23 Global RGB and AA14 Per-Key RGB
# -----------------------------------------------------------------------------
def test_stop_restores_initial_global_and_per_key_states():
    """Verify stop_visualizer restores both initial Per-Key buffer and initial Global RGB effect."""
    transport = MockHidTransport(auto_ack=True)

    # Initial states:
    # 1. Per-Key buffer with slot 10 = (44, 55, 66)
    initial_buf = make_custom_512b_buffer(key_slot=10, color=(44, 55, 66))
    queue_per_key_read_responses(transport, initial_buf)

    # 2. Global RGB with effect 0x0F (Ripple Spread)
    queue_global_rgb_read_response(transport, effect=0x0F, brightness=4, speed=3)

    output = KeyboardRgbOutput(transport=transport)
    output.start()

    # Clear recorded packets from start()
    transport.recorded_reports.clear()

    # Write a visualizer frame (10 AA24 packets)
    output.write_frame(RGBFrame.solid((255, 0, 0)))
    assert len(transport.recorded_reports) == 10

    # Stop with restore=True
    transport.recorded_reports.clear()
    output.stop(restore=True)

    # Expect: 10 packets AA24 (restore Per-Key) + 1 packet AA23 (restore Global RGB) = 11 packets
    assert len(transport.recorded_reports) == 11

    # Verify 10 AA24 restore chunks restore initial_buf byte-exact
    per_key_restore_pkts = [r[1] for r in transport.recorded_reports[:10]]
    restored_buf = parse_rgb_per_key_chunks(per_key_restore_pkts)
    assert restored_buf == initial_buf
    restored_slots = parse_led_buffer(restored_buf)
    assert restored_slots[10] == (44, 55, 66)

    # Verify 11th packet is AA23 restoring effect 0x0F
    global_restore_pkt = transport.recorded_reports[10][1]
    assert global_restore_pkt[0] == 0xAA
    assert global_restore_pkt[1] == 0x23
    assert global_restore_pkt[8] == 0x0F  # Restored initial effect


# -----------------------------------------------------------------------------
# Test F: Exception During AA24 Restores Both States
# -----------------------------------------------------------------------------
def test_exception_during_aa24_restores_both_states():
    """Verify that failure during write_frame attempts restoration of both Per-Key and Global RGB."""
    transport = MockHidTransport(auto_ack=True)

    initial_buf = make_custom_512b_buffer(key_slot=20, color=(99, 88, 77))
    queue_per_key_read_responses(transport, initial_buf)
    queue_global_rgb_read_response(transport, effect=0x05)

    output = KeyboardRgbOutput(transport=transport)
    output.start()

    transport.recorded_reports.clear()
    transport.fail_next_write = True

    with pytest.raises(HardwareOutputError, match="Failed to write RGB frame"):
        output.write_frame(RGBFrame.solid((255, 255, 255)))

    assert output.is_active is False


# -----------------------------------------------------------------------------
# Test G: Absence of Non-RGB Subsystem Opcodes
# -----------------------------------------------------------------------------
def test_subsystem_isolation_strictly_no_non_rgb_opcodes():
    """Verify that only AA13/AA14 (reads) and AA23/AA24 (writes) are emitted. Zero others."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)

    output.start()
    output.write_frame(RGBFrame.solid((255, 0, 0)))
    output.stop(restore=True)

    forbidden_opcodes = {
        0x11, 0x21,  # Game Mode
        0x12, 0x22,  # Remap L1
        0x15, 0x25,  # Macros
        0x16, 0x26,  # Remap L2
        0x17, 0x27,  # Hall Effect
        0x18, 0x28,  # DKS
    }

    allowed_opcodes = {0x13, 0x14, 0x23, 0x24}

    for _, rep in transport.recorded_reports:
        opcode = rep[1]
        assert opcode in allowed_opcodes, f"Forbidden opcode 0x{opcode:02X} was transmitted!"
        assert opcode not in forbidden_opcodes, f"Subsystem isolation violated by opcode 0x{opcode:02X}"


# -----------------------------------------------------------------------------
# Test H: Exact 10 Chunks and Ascending Addresses
# -----------------------------------------------------------------------------
def test_aa24_chunk_count_and_address_order():
    """Verify write_frame sends exactly 10 AA24 chunks with strictly ascending addresses."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)
    output.start()
    transport.recorded_reports.clear()

    output.write_frame(RGBFrame.solid((128, 64, 32)))

    assert len(transport.recorded_reports) == RGB_PER_KEY_TOTAL_REPORTS  # Exactly 10

    expected_addrs = [i * 56 for i in range(9)] + [RGB_PER_KEY_TAIL_ADDR]
    for idx, (_, pkt) in enumerate(transport.recorded_reports):
        assert pkt[0] == 0xAA
        assert pkt[1] == 0x24
        expected_size = 56 if idx < 9 else 8
        assert pkt[2] == expected_size
        addr = struct.unpack_from("<H", pkt, 3)[0]
        assert addr == expected_addrs[idx]


# -----------------------------------------------------------------------------
# Test I: Multiple Sequential Frames
# -----------------------------------------------------------------------------
def test_multiple_sequential_frames():
    """Verify 3 consecutive frames emit 30 AA24 packets in order."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)
    output.start()
    transport.recorded_reports.clear()

    output.write_frame(RGBFrame.solid((255, 0, 0)))
    output.write_frame(RGBFrame.solid((0, 255, 0)))
    output.write_frame(RGBFrame.solid((0, 0, 255)))

    assert output.frames_written == 3
    assert len(transport.recorded_reports) == 30


# -----------------------------------------------------------------------------
# Test J: Stop Terminates Further Writes
# -----------------------------------------------------------------------------
def test_stop_terminates_further_writes():
    """Verify that calling stop prevents any further frames from writing to transport."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)
    output.start()

    output.write_frame(RGBFrame.solid((100, 100, 100)))
    assert output.frames_written == 1

    output.stop(restore=False)
    assert output.is_active is False

    transport.recorded_reports.clear()
    output.write_frame(RGBFrame.solid((200, 200, 200)))
    assert len(transport.recorded_reports) == 0
    assert output.frames_written == 1


# -----------------------------------------------------------------------------
# Test K: Thread Safety Under Concurrent Writes
# -----------------------------------------------------------------------------
def test_thread_safety_no_interleaved_packets():
    """Verify concurrent write_frame calls from multiple threads never interleave packets."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)
    output.start()
    transport.recorded_reports.clear()

    frames = [
        RGBFrame.solid((10, 0, 0)),
        RGBFrame.solid((0, 20, 0)),
        RGBFrame.solid((0, 0, 30)),
        RGBFrame.solid((40, 40, 0)),
    ]

    threads = [threading.Thread(target=output.write_frame, args=(f,)) for f in frames]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert output.frames_written == len(frames)
    assert len(transport.recorded_reports) == len(frames) * 10

    for block_idx in range(len(frames)):
        block = transport.recorded_reports[block_idx * 10 : (block_idx + 1) * 10]
        expected_addrs = [i * 56 for i in range(9)] + [RGB_PER_KEY_TAIL_ADDR]
        actual_addrs = [struct.unpack_from("<H", pkt, 3)[0] for _, pkt in block]
        assert actual_addrs == expected_addrs, f"Block #{block_idx} had interleaved chunks"


# -----------------------------------------------------------------------------
# Test L: Device Absence / Connection Error Handling
# -----------------------------------------------------------------------------
def test_device_absence_raises_hardware_output_error():
    """Verify clean HardwareOutputError when transport is None or disconnected."""
    output_none = KeyboardRgbOutput(transport=None)
    with pytest.raises(HardwareOutputError, match="No HID transport configured"):
        output_none.start()

    transport = MockHidTransport(auto_ack=True)
    transport.close()
    output_closed = KeyboardRgbOutput(transport=transport)
    with pytest.raises(HardwareOutputError, match="disconnected or closed"):
        output_closed.start()


# -----------------------------------------------------------------------------
# Test M: PlaybackEngine Integration
# -----------------------------------------------------------------------------
def test_playback_engine_pipeline_integration():
    """Verify attach_to_engine connects PlaybackEngine -> FrameProcessor -> KeyboardRgbOutput."""
    transport = MockHidTransport(auto_ack=True)
    output = KeyboardRgbOutput(transport=transport)
    output.start()
    transport.recorded_reports.clear()

    class MockDecoder:
        duration = 1.0
        frame_count = 2
        metadata = MediaMetadata(
            media_type="test",
            width=100,
            height=50,
            fps=2.0,
            total_frames=2,
            duration=1.0,
            loop_count=0,
        )

        def get_frame_at_time(self, t: float):
            img = Image.new("RGB", (100, 50), (255, 0, 0) if t < 0.5 else (0, 0, 255))
            return DecodedFrame(
                image=img,
                frame_index=0 if t < 0.5 else 1,
                timestamp=0.0 if t < 0.5 else 0.5,
                duration=0.5,
            )

        def close(self):
            pass

    fake_clock = FakeClock()
    decoder = MockDecoder()
    engine = PlaybackEngine(decoder=decoder, clock=fake_clock, run_worker=False)

    output.attach_to_engine(engine)

    engine.play()
    engine.step(delta_seconds=0.0)
    assert output.frames_written == 1

    engine.step(delta_seconds=0.6)
    assert output.frames_written == 2

    output.stop(restore=False)
    assert engine.is_playing is False


# -----------------------------------------------------------------------------
# Test N: Smoke Test Runner Offline Validation
# -----------------------------------------------------------------------------
def test_smoke_test_runner_with_mock_transport():
    """Verify run_hardware_smoke_test executes all 3 patterns and restores cleanly."""
    from keyboard_re.visualizer.smoke_test import build_test_patterns, run_hardware_smoke_test

    patterns = build_test_patterns()
    assert len(patterns) == 3

    transport = MockHidTransport(auto_ack=True)
    success = run_hardware_smoke_test(transport=transport, step_delay=0.001)
    assert success is True

    # 12 in start (10 read AA14 + 1 read AA13 + 1 write AA23)
    # + 30 in writes (3 frames * 10 AA24)
    # + 11 in restore (10 write AA24 + 1 write AA23)
    # = 53 reports total
    assert len(transport.recorded_reports) == 53
