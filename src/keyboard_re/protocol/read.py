"""
Read / State-Sync Protocol Parser & Request Encoder for IO by Red Square Type 84 Magnetic Black.

Decodes incoming device responses (DEVICE -> HOST, prefix 55 xx):
- Handshake / Device info response (55 10)
- GameMode / Performance settings response (55 11)
- Key Remapping Layer 1 & 2 (55 12 & 55 16)
- Global RGB state response (55 13)
- Per-Key RGB LED matrix response (55 14)
- Macro table response (55 15)
- Hall effect analog configuration image (55 17)
- DKS (Dynamic Keystroke) table (55 18)

Encodes offline read requests (HOST -> DEVICE, prefix AA 1x).
NO direct hardware I/O or USB transmissions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence, Tuple

from keyboard_re.protocol.packets import REPORT_SIZE, pad_report, validate_report_size
from keyboard_re.protocol.rgb import RGBGlobalConfig
from keyboard_re.protocol.transport import HidTransport

DKS_BUFFER_SIZE = 1024  # 64 records * 16 bytes
DKS_RECORD_COUNT = 64
DKS_RECORD_SIZE = 16


@dataclass
class DeviceInfoResponse:
    """Hardware identity & firmware version returned in handshake (55 10)."""
    raw_header: bytes
    chip_id: bytes
    vid: int
    pid: int
    firmware_version: str
    bootloader_version: str


@dataclass
class GameModeResponse:
    """
    Performance and system settings returned by GET_GAME_MODE (55 11) / SET_GAME_MODE (AA 21).

    CONFIRMED fields from vendor JS analysis & hardware captures:
    - game_mode: Game mode / Win lock (payload[1])
    - fn_switch: Fn switch inversion (payload[2])
    - sleep_time: Timeout before entering sleep mode in minutes (payload[3] / report[11])
    - key_delay: Key debounce delay in ms (payload[4])
    - report_rate: Polling rate identifier (payload[5] / report[13], e.g. 3=1000Hz, 5=4000Hz, 6=8000Hz)
    - system_mode: OS mode (payload[6], 0=Windows, 1=Mac)
    - tft_display_time: Screen timeout (payload[7])
    - top_deadzone: Top deadzone in mm (payload[8] / 100.0)
    - bottom_deadzone: Bottom deadzone in mm (payload[9] / 100.0)
    - stability_mode: Stability mode enabled/disabled (payload[11] / report[19])
    - auto_calibration: Automatic calibration enabled/disabled (payload[14] / report[22])
    - single_key_wakeup: Wake up from single key (payload[15])
    - push_button_mode: Push button mode (payload[16])

    Raw payload preserved for byte-exact round-trips.
    """
    sleep_time: int = 1
    report_rate: int = 6
    stability_mode: int = 1
    auto_calibration: int = 1
    raw_payload: bytes = b""
    game_mode: int = 0
    fn_switch: int = 0
    key_delay: int = 0
    system_mode: int = 0
    tft_display_time: int = 0
    top_deadzone: float = 0.0
    bottom_deadzone: float = 0.0
    single_key_wakeup: int = 0
    push_button_mode: int = 0
    full_payload: bytes = b""

    def to_payload(self) -> bytes:
        """Serialize settings into the canonical 56-byte payload for AA 21 write."""
        if self.full_payload and len(self.full_payload) == 56:
            buf = bytearray(self.full_payload)
        elif self.raw_payload and len(self.raw_payload) >= 56:
            buf = bytearray(self.raw_payload[:56])
        elif self.raw_payload and len(self.raw_payload) >= 16:
            buf = bytearray(56)
            buf[:len(self.raw_payload)] = self.raw_payload
        else:
            buf = bytearray(56)

        buf[1] = int(self.game_mode) & 0xFF
        buf[2] = int(self.fn_switch) & 0xFF
        buf[3] = int(self.sleep_time) & 0xFF
        buf[4] = int(self.key_delay) & 0xFF
        buf[5] = int(self.report_rate) & 0xFF
        buf[6] = int(self.system_mode) & 0xFF
        buf[7] = int(self.tft_display_time) & 0xFF
        buf[8] = int(round(self.top_deadzone * 100)) & 0xFF
        buf[9] = int(round(self.bottom_deadzone * 100)) & 0xFF
        buf[11] = int(self.stability_mode) & 0xFF
        buf[14] = int(self.auto_calibration) & 0xFF
        buf[15] = int(self.single_key_wakeup) & 0xFF
        buf[16] = int(self.push_button_mode) & 0xFF
        return bytes(buf)

    def clone(self) -> GameModeResponse:
        """Return a deep copy of this configuration."""
        return GameModeResponse(
            sleep_time=self.sleep_time,
            report_rate=self.report_rate,
            stability_mode=self.stability_mode,
            auto_calibration=self.auto_calibration,
            raw_payload=bytes(self.raw_payload),
            game_mode=self.game_mode,
            fn_switch=self.fn_switch,
            key_delay=self.key_delay,
            system_mode=self.system_mode,
            tft_display_time=self.tft_display_time,
            top_deadzone=self.top_deadzone,
            bottom_deadzone=self.bottom_deadzone,
            single_key_wakeup=self.single_key_wakeup,
            push_button_mode=self.push_button_mode,
            full_payload=bytes(self.full_payload) if self.full_payload else b"",
        )

    @property
    def report_rate_hz(self) -> int:
        """Convert vendor reportRate code to frequency in Hz."""
        return {3: 1000, 4: 2000, 5: 4000, 6: 8000}.get(self.report_rate, 8000)

    def set_report_rate_hz(self, hz: int) -> None:
        """Set reportRate code from frequency in Hz (1000, 4000, 8000)."""
        mapping = {1000: 3, 2000: 4, 4000: 5, 8000: 6}
        if hz in mapping:
            self.report_rate = mapping[hz]

    # Backwards compatibility properties:
    @property
    def active_profile(self) -> int:
        """Deprecated: byte 11 is sleepTime, not active profile. Retained for backwards compatibility."""
        return self.sleep_time

    @property
    def raw_status(self) -> bytes:
        return self.raw_payload

    @property
    def payload_16b(self) -> bytes:
        return self.raw_payload

    @property
    def lock_flags(self) -> int:
        return self.report_rate

    @property
    def mode_flag(self) -> int:
        return self.stability_mode

    @property
    def connection_flag(self) -> int:
        return self.auto_calibration


# Backwards compatibility alias
KeyboardStatusResponse = GameModeResponse


def parse_handshake_response(report: bytes | bytearray) -> DeviceInfoResponse:
    """
    Parse a 64-byte handshake response (55 10 30 ...).

    Layout:
    - Bytes 0..2: 55 10 30
    - Bytes 3..7: 00 00 00 01 00
    - Bytes 8..11: Chip / Hardware identifier
    - Bytes 12..13: VID (uint16_le, e.g. 0x0C45)
    - Bytes 14..15: PID (uint16_le, e.g. 0x80D6)
    - Bytes 16..17: Firmware version (uint16_le, e.g. 0x0117 -> v1.17)
    - Bytes 20..21: Bootloader version (uint16_le, e.g. 0x0166 -> v1.66)
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(b"\x55\x10"):
        raise ValueError(f"Invalid handshake response prefix: {rep[:2].hex().upper()}")

    chip_id = rep[8:12]
    vid = struct.unpack_from("<H", rep, 12)[0]
    pid = struct.unpack_from("<H", rep, 14)[0]

    fw_raw = struct.unpack_from("<H", rep, 16)[0]
    fw_ver = f"{fw_raw >> 8}.{fw_raw & 0xFF:02x}"

    bl_raw = struct.unpack_from("<H", rep, 20)[0]
    bl_ver = f"{bl_raw >> 8}.{bl_raw & 0xFF:02x}"

    return DeviceInfoResponse(
        raw_header=rep[:8],
        chip_id=chip_id,
        vid=vid,
        pid=pid,
        firmware_version=fw_ver,
        bootloader_version=bl_ver,
    )


def parse_game_mode_response(report: bytes | bytearray) -> GameModeResponse:
    """
    Parse a 64-byte GameMode response (55 11 38 ...).

    Confirmed fields:
    - Offset 9 (payload 1): gameMode (0/1)
    - Offset 10 (payload 2): fnSwitch (0/1)
    - Offset 11 (payload 3): sleepTime (minutes before sleep)
    - Offset 12 (payload 4): keyDelay (ms debounce)
    - Offset 13 (payload 5): reportRate (3=1000Hz, 5=4000Hz, 6=8000Hz)
    - Offset 14 (payload 6): systemMode (0=Win, 1=Mac)
    - Offset 15 (payload 7): tftDisplayTime (timeout)
    - Offset 16 (payload 8): topDeadZone (mm * 100)
    - Offset 17 (payload 9): bottomDeadZone (mm * 100)
    - Offset 19 (payload 11): stabilityMode (1 = enabled)
    - Offset 22 (payload 14): autoCalibration (1 = enabled)
    - Offset 23 (payload 15): singleKeyWakeup (0/1)
    - Offset 24 (payload 16): pushButtonMode (0/1)
    - Offsets 8..24: raw 16-byte payload preserved for backwards compatibility
    - Offsets 8..64: full 56-byte payload preserved for byte-exact roundtrips
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(b"\x55\x11"):
        raise ValueError(f"Invalid GameMode response prefix: {rep[:2].hex().upper()}")

    full_payload = rep[8:64]
    payload_16b = rep[8:24]

    game_mode = full_payload[1] if len(full_payload) > 1 else 0
    fn_switch = full_payload[2] if len(full_payload) > 2 else 0
    sleep_time = full_payload[3] if len(full_payload) > 3 else rep[11]
    key_delay = full_payload[4] if len(full_payload) > 4 else 0
    report_rate = full_payload[5] if len(full_payload) > 5 else rep[13]
    system_mode = full_payload[6] if len(full_payload) > 6 else 0
    tft_display_time = full_payload[7] if len(full_payload) > 7 else 0
    top_deadzone = (full_payload[8] / 100.0) if len(full_payload) > 8 else 0.0
    bottom_deadzone = (full_payload[9] / 100.0) if len(full_payload) > 9 else 0.0
    stability_mode = full_payload[11] if len(full_payload) > 11 else rep[19]
    auto_calibration = full_payload[14] if len(full_payload) > 14 else rep[22]
    single_key_wakeup = full_payload[15] if len(full_payload) > 15 else 0
    push_button_mode = full_payload[16] if len(full_payload) > 16 else 0

    return GameModeResponse(
        sleep_time=sleep_time,
        report_rate=report_rate,
        stability_mode=stability_mode,
        auto_calibration=auto_calibration,
        raw_payload=payload_16b,
        game_mode=game_mode,
        fn_switch=fn_switch,
        key_delay=key_delay,
        system_mode=system_mode,
        tft_display_time=tft_display_time,
        top_deadzone=top_deadzone,
        bottom_deadzone=bottom_deadzone,
        single_key_wakeup=single_key_wakeup,
        push_button_mode=push_button_mode,
        full_payload=full_payload,
    )


def parse_status_response(report: bytes | bytearray) -> GameModeResponse:
    """Backwards compatibility alias for parse_game_mode_response."""
    return parse_game_mode_response(report)


def parse_rgb_global_read_response(report: bytes | bytearray) -> RGBGlobalConfig:
    """
    Parse a 64-byte Global RGB read response (55 13 10 ...).
    """
    validate_report_size(report, REPORT_SIZE)
    rep = bytes(report)

    if not rep.startswith(b"\x55\x13\x10"):
        raise ValueError(f"Invalid Global RGB read response prefix: {rep[:3].hex().upper()}")

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
        magic=rep[22:24],
        raw_payload=rep,
        reserved_mid=rep[15:17],
        reserved_tail=rep[19:22],
    )


def parse_rgb_per_key_read_chunks(reports: Sequence[bytes | bytearray]) -> bytes:
    """
    Parse a 10-report sequence of Per-Key RGB responses (55 14 38 ... & 55 14 08 ...)
    into the 512-byte LED buffer.
    """
    if len(reports) != 10:
        raise ValueError(f"Expected 10 reports for Per-Key RGB read, got {len(reports)}")

    buf = bytearray(512)

    # 1. First 9 chunks (56 bytes each)
    for i in range(9):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if not rep.startswith(b"\x55\x14\x38"):
            raise ValueError(f"Report #{i}: invalid chunk prefix, expected 55 14 38")
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * 56
        if addr != expected_addr:
            raise ValueError(f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}")
        buf[addr : addr + 56] = rep[8 : 8 + 56]

    # 2. Final tail chunk (8 bytes at 0x01F8 = 504)
    tail = bytes(reports[9])
    validate_report_size(tail, REPORT_SIZE)
    if not tail.startswith(b"\x55\x14\x08"):
        raise ValueError("Report #9: invalid tail chunk prefix, expected 55 14 08")
    tail_addr = struct.unpack_from("<H", tail, 3)[0]
    if tail_addr != 504:
        raise ValueError(f"Report #9: tail address mismatch, expected 0x01F8, got 0x{tail_addr:04X}")
    buf[504 : 504 + 8] = tail[8 : 8 + 8]

    return bytes(buf)


def parse_hall_read_chunks(
    reports: Sequence[bytes | bytearray],
    expected_opcode: int = 0x17,
) -> bytes:
    """
    Parse an 18-report (or 19-report with terminator) sequence of Hall configuration read responses
    (55 17 38 ... & 55 17 10 ..., or 55 18 38 ... for Profile 2) into the 1008-byte configuration image.
    """
    if len(reports) not in (18, 19):
        raise ValueError(f"Expected 18 or 19 reports for Hall read response, got {len(reports)}")

    expected_prefix = bytes([0x55, expected_opcode, 0x38])
    buf = bytearray(1008)
    for i in range(18):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if not rep.startswith(expected_prefix):
            raise ValueError(f"Report #{i}: invalid Hall read chunk prefix, expected 55 {expected_opcode:02X} 38")
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * 56
        if addr != expected_addr:
            raise ValueError(f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}")
        buf[addr : addr + 56] = rep[8 : 8 + 56]

    return bytes(buf)


def parse_dks_read_chunks(reports: Sequence[bytes | bytearray]) -> bytes:
    """
    Parse a 19-report sequence of DKS read responses (55 18 38 ... & 55 18 10 ...)
    into the 1024-byte DKS table (64 records * 16 bytes).
    """
    if len(reports) not in (18, 19):
        raise ValueError(f"Expected 18 or 19 reports for DKS read response, got {len(reports)}")

    buf = bytearray(DKS_BUFFER_SIZE)
    for i in range(18):
        rep = bytes(reports[i])
        validate_report_size(rep, REPORT_SIZE)
        if not rep.startswith(b"\x55\x18\x38"):
            raise ValueError(f"Report #{i}: invalid DKS read chunk prefix, expected 55 18 38")
        addr = struct.unpack_from("<H", rep, 3)[0]
        expected_addr = i * 56
        if addr != expected_addr:
            raise ValueError(f"Report #{i}: address mismatch, expected 0x{expected_addr:04X}, got 0x{addr:04X}")
        buf[addr : addr + 56] = rep[8 : 8 + 56]

    if len(reports) == 19:
        rep18 = bytes(reports[18])
        validate_report_size(rep18, REPORT_SIZE)
        if not rep18.startswith(b"\x55\x18\x10"):
            raise ValueError("Report #18: invalid DKS tail chunk prefix, expected 55 18 10")
        addr18 = struct.unpack_from("<H", rep18, 3)[0]
        if addr18 != 1008:
            raise ValueError(f"Report #18: tail address mismatch, expected 0x03F0 (1008), got 0x{addr18:04X}")
        buf[1008 : 1008 + 16] = rep18[8 : 8 + 16]

    return bytes(buf)


def build_read_request(
    opcode_low_nibble: int,
    address: int = 0,
    chunk_size: int = 56,
    sub_header: bytes = b"\x00\x00\x00\x00\x00",
) -> bytes:
    """
    Build a 64-byte offline read query packet (AA 1x ...).

    Format:
    - Byte 0: AA (Host prefix)
    - Byte 1: 1x (Read command, e.g. 0x17 for Hall read, 0x14 for Per-Key RGB read)
    - Byte 2: Chunk size (e.g. 0x38 = 56 bytes, 0x10 = 16 bytes, 0x08 = 8 bytes)
    - Bytes 3..4: Address (uint16_le)
    - Bytes 5..9: Sub-header
    - Bytes 10..63: Zero padding
    """
    cmd = 0x10 | (opcode_low_nibble & 0x0F)
    prefix = bytes([0xAA, cmd, chunk_size & 0xFF]) + struct.pack("<H", address)
    raw = prefix + sub_header
    return pad_report(raw, REPORT_SIZE)


def parse_calibration_response(reports: Sequence[bytes | bytearray]):
    """
    Parse AA 1C / 55 1C response chunks.
    
    Preserved as raw keymap/calibration table (512 bytes).
    """
    from keyboard_re.protocol.keymap import parse_keymap_chunks
    return parse_keymap_chunks(reports, expected_opcode=0x1C, layer=3)


def read_device_info(transport: HidTransport, timeout: float = 1.0) -> DeviceInfoResponse:
    """Read device identity and firmware version via AA 10."""
    req = bytearray(REPORT_SIZE)
    req[:8] = bytes.fromhex("AA 10 30 00 00 00 01 00")
    transport.send_report(0, bytes(req))
    resp = transport.receive_report(timeout=timeout)
    return parse_handshake_response(resp)


def read_game_mode(transport: HidTransport, timeout: float = 1.0) -> GameModeResponse:
    """Read performance and game mode configuration via AA 11."""
    req = bytearray(REPORT_SIZE)
    req[:8] = bytes.fromhex("AA 11 38 00 00 00 01 00")
    transport.send_report(0, bytes(req))
    resp = transport.receive_report(timeout=timeout)
    return parse_game_mode_response(resp)


def read_keyboard_status(transport: HidTransport, timeout: float = 1.0) -> GameModeResponse:
    """Backwards compatibility alias for read_game_mode."""
    return read_game_mode(transport, timeout=timeout)


def read_hall_profile(
    transport: HidTransport,
    profile_id: int = 1,
    timeout: float = 1.0,
) -> bytes:
    """
    Read the complete 1008-byte Hall analog configuration image for Profile 1 (AA 17).
    Issues 18 data read chunks + 1 terminator and returns the 1008-byte image.
    """
    opcode = 0x17
    buf = bytearray(1008)

    for i in range(18):
        addr = i * 56
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = opcode
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(
            timeout=timeout,
            expected_opcode=opcode,
            expected_address=addr,
        )

        validate_report_size(resp, REPORT_SIZE)
        if resp[0] != 0x55 or resp[1] != opcode or resp[2] != 0x38:
            raise ValueError(
                f"Chunk #{i}: expected prefix 55 {opcode:02X} 38, got {resp[:3].hex(' ').upper()}"
            )
        resp_addr = struct.unpack_from("<H", resp, 3)[0]
        if resp_addr != addr:
            raise ValueError(f"Chunk #{i}: address mismatch: expected 0x{addr:04X}, got 0x{resp_addr:04X}")

        buf[addr : addr + 56] = resp[8 : 8 + 56]

    # Read terminator
    req_term = bytearray(REPORT_SIZE)
    req_term[0] = 0xAA
    req_term[1] = opcode
    req_term[2] = 0x10
    struct.pack_into("<H", req_term, 3, 0x03F0)
    req_term[5:8] = bytes([0x00, 0x01, 0x00])
    transport.send_report(0, bytes(req_term))
    resp_term = transport.receive_report(
        timeout=timeout,
        expected_opcode=opcode,
    )

    validate_report_size(resp_term, REPORT_SIZE)
    if resp_term[0] != 0x55 or resp_term[1] != opcode:
        raise ValueError(
            f"Hall terminator: expected prefix 55 {opcode:02X}, got {resp_term[:3].hex(' ').upper()}"
        )

    return bytes(buf)


def read_dks_table(transport: HidTransport, timeout: float = 1.0) -> bytes:
    """
    Read the complete 1024-byte DKS configuration table via AA 18.
    Issues 18 data read chunks (56B) + 1 tail chunk (16B at 0x03F0).
    """
    buf = bytearray(DKS_BUFFER_SIZE)

    for i in range(18):
        addr = i * 56
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = 0x18
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)

        validate_report_size(resp, REPORT_SIZE)
        if resp[0] != 0x55 or resp[1] != 0x18 or resp[2] != 0x38:
            raise ValueError(
                f"DKS chunk #{i}: expected prefix 55 18 38, got {resp[:3].hex(' ').upper()}"
            )
        resp_addr = struct.unpack_from("<H", resp, 3)[0]
        if resp_addr != addr:
            raise ValueError(f"DKS chunk #{i}: address mismatch: expected 0x{addr:04X}, got 0x{resp_addr:04X}")

        buf[addr : addr + 56] = resp[8 : 8 + 56]

    # Read tail chunk (16 bytes at 0x03F0 = 1008)
    req_tail = bytearray(REPORT_SIZE)
    req_tail[0] = 0xAA
    req_tail[1] = 0x18
    req_tail[2] = 0x10
    struct.pack_into("<H", req_tail, 3, 0x03F0)
    req_tail[6] = 0x01  # isLastPacket flag
    transport.send_report(0, bytes(req_tail))
    resp_tail = transport.receive_report(timeout=timeout)

    validate_report_size(resp_tail, REPORT_SIZE)
    if resp_tail[0] != 0x55 or resp_tail[1] != 0x18 or resp_tail[2] != 0x10:
        raise ValueError(
            f"DKS tail chunk: expected prefix 55 18 10, got {resp_tail[:3].hex(' ').upper()}"
        )
    resp_tail_addr = struct.unpack_from("<H", resp_tail, 3)[0]
    if resp_tail_addr != 1008:
        raise ValueError(f"DKS tail chunk: address mismatch: expected 0x03F0, got 0x{resp_tail_addr:04X}")

    buf[1008 : 1008 + 16] = resp_tail[8 : 8 + 16]
    return bytes(buf)


def read_keymap_table(
    transport: HidTransport,
    opcode: int = 0x12,
    timeout: float = 1.0,
) -> bytes:
    """
    Read the complete 512-byte keymap table for Layer 1 (AA 12), Layer 2 (AA 16), or Layer 3 (AA 1C).
    Issues 9 chunks of 56 bytes + 1 tail chunk of 8 bytes (10 reports total).
    """
    buf = bytearray(512)

    # 1. 9 chunks of 56 bytes (0..503)
    for i in range(9):
        addr = i * 56
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = opcode
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)

        validate_report_size(resp, REPORT_SIZE)
        if resp[0] != 0x55 or resp[1] != opcode or resp[2] != 0x38:
            raise ValueError(
                f"Keymap chunk #{i}: expected prefix 55 {opcode:02X} 38, got {resp[:3].hex(' ').upper()}"
            )
        resp_addr = struct.unpack_from("<H", resp, 3)[0]
        if resp_addr != addr:
            raise ValueError(f"Keymap chunk #{i}: address mismatch: expected 0x{addr:04X}, got 0x{resp_addr:04X}")

        buf[addr : addr + 56] = resp[8 : 8 + 56]

    # 2. Tail chunk: 8 bytes at 0x01F8 = 504
    req_tail = bytearray(REPORT_SIZE)
    req_tail[0] = 0xAA
    req_tail[1] = opcode
    req_tail[2] = 0x08
    struct.pack_into("<H", req_tail, 3, 504)
    req_tail[6] = 0x01  # isLastPacket flag
    transport.send_report(0, bytes(req_tail))
    resp_tail = transport.receive_report(timeout=timeout)

    validate_report_size(resp_tail, REPORT_SIZE)
    if resp_tail[0] != 0x55 or resp_tail[1] != opcode or resp_tail[2] != 0x08:
        raise ValueError(
            f"Keymap tail chunk: expected prefix 55 {opcode:02X} 08, got {resp_tail[:3].hex(' ').upper()}"
        )
    tail_addr = struct.unpack_from("<H", resp_tail, 3)[0]
    if tail_addr != 504:
        raise ValueError(f"Keymap tail chunk: address mismatch: expected 0x01F8, got 0x{tail_addr:04X}")

    buf[504 : 504 + 8] = resp_tail[8 : 8 + 8]
    return bytes(buf)


def read_rgb_global(transport: HidTransport, timeout: float = 1.0) -> RGBGlobalConfig:
    """Read Global RGB configuration via AA 13."""
    req = bytearray(REPORT_SIZE)
    req[:8] = bytes.fromhex("AA 13 10 00 00 00 01 00")
    transport.send_report(0, bytes(req))
    resp = transport.receive_report(timeout=timeout)
    return parse_rgb_global_read_response(resp)


def read_rgb_per_key(transport: HidTransport, timeout: float = 1.0) -> bytes:
    """Read the complete 512-byte Per-Key RGB LED matrix table via AA 14."""
    reports = []
    for i in range(9):
        addr = i * 56
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = 0x14
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)
        reports.append(resp)
    req_tail = bytearray(REPORT_SIZE)
    req_tail[0] = 0xAA
    req_tail[1] = 0x14
    req_tail[2] = 0x08
    struct.pack_into("<H", req_tail, 3, 504)
    req_tail[5:8] = bytes([0x00, 0x01, 0x00])
    transport.send_report(0, bytes(req_tail))
    resp_tail = transport.receive_report(timeout=timeout)
    reports.append(resp_tail)
    return parse_rgb_per_key_read_chunks(reports)


def read_macro_table(transport: HidTransport, timeout: float = 1.0) -> bytes:
    """
    Read the complete 400-byte Macro / DKS buffer via AA 15.
    Issues 7 chunks of 56 bytes + 1 tail chunk of 8 bytes (8 reports total).
    """
    buf = bytearray(400)

    # 1. 7 chunks of 56 bytes (0..391)
    for i in range(7):
        addr = i * 56
        req = bytearray(REPORT_SIZE)
        req[0] = 0xAA
        req[1] = 0x15
        req[2] = 0x38
        struct.pack_into("<H", req, 3, addr)
        transport.send_report(0, bytes(req))
        resp = transport.receive_report(timeout=timeout)

        validate_report_size(resp, REPORT_SIZE)
        if resp[0] != 0x55 or resp[1] != 0x15 or resp[2] != 0x38:
            raise ValueError(
                f"Macro chunk #{i}: expected prefix 55 15 38, got {resp[:3].hex(' ').upper()}"
            )
        resp_addr = struct.unpack_from("<H", resp, 3)[0]
        if resp_addr != addr:
            raise ValueError(f"Macro chunk #{i}: address mismatch: expected 0x{addr:04X}, got 0x{resp_addr:04X}")

        buf[addr : addr + 56] = resp[8 : 8 + 56]

    # 2. Tail chunk: 8 bytes at 0x0188 = 392
    req_tail = bytearray(REPORT_SIZE)
    req_tail[0] = 0xAA
    req_tail[1] = 0x15
    req_tail[2] = 0x08
    struct.pack_into("<H", req_tail, 3, 392)
    req_tail[5:8] = bytes([0x00, 0x01, 0x00])
    transport.send_report(0, bytes(req_tail))
    resp_tail = transport.receive_report(timeout=timeout)

    validate_report_size(resp_tail, REPORT_SIZE)
    if resp_tail[0] != 0x55 or resp_tail[1] != 0x15 or resp_tail[2] != 0x08:
        raise ValueError(
            f"Macro tail chunk: expected prefix 55 15 08, got {resp_tail[:3].hex(' ').upper()}"
        )
    tail_addr = struct.unpack_from("<H", resp_tail, 3)[0]
    if tail_addr != 392:
        raise ValueError(f"Macro tail chunk: address mismatch: expected 0x0188, got 0x{tail_addr:04X}")

    buf[392 : 392 + 8] = resp_tail[8 : 8 + 8]
    return bytes(buf)



