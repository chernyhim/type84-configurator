# RGB Per-Key Offset Bug Closure Report (BUG-1, BUG-2, BUG-3)

**Target Hardware:** IO by Red Square Type 84 Magnetic Black (`0x0C45:0x80D6`)  
**Status:** CLOSED  
**Date:** 2026-09-17  
**Test Suite:** 459 passed, 6 skipped (100% PASS)  
**Physical ABA Validation:** PASS (Exact Target Readback, Final SHA == Baseline SHA, Zero Cross-Subsystem Contamination, Forbidden Writes = 0)

---

## 1. Executive Summary

During the pre-release audit of HID packet layouts (`hid_offset_audit.md` / `tests/test_hid_offset_regression.py`), three correlated protocol offset bugs were identified in the RGB Per-Key subsystem:
- **BUG-1 (`read.py`):** `parse_rgb_per_key_read_chunks` read incoming `55 14` packets starting at offset 5 instead of offset 8, shifting the entire 512-byte LED buffer by 3 bytes and injecting sub-header metadata into the color channels.
- **BUG-2 (`rgb.py`):** `build_rgb_per_key_chunks` assembled outgoing `AA 24` packets with payload placed at offset 5 rather than canonical offset 8; symmetrically, `parse_rgb_per_key_chunks` read from offset 5, concealing the offset error in round-trip tests while causing corrupt writes to real hardware.
- **BUG-3 (`transport.py`):** `MockHidTransport.send_report` auto-ACK mirrored payload at `ack[5:5+sz] = data[5:5+sz]` instead of `ack[8:8+sz] = data[8:8+sz]`, mirroring the legacy offset-5 bug in mock environments.

All three confirmed bugs have been corrected strictly at their root cause. No other protocol modules (`macro.py`, `hall.py`, `game_mode.py`, `keymap.py`, `calibration.py`) were modified.

The full test suite passed (459 passed, 6 skipped). Following the green suite, a physical hardware closed-loop A -> B -> A test was performed on the connected Type 84 keyboard under strict write-filtering (`SafetyFilteredTransport`), confirming:
1. **Target readback byte-exact** (mutated LED 35 to Green; verified exact readback of `[35, 0, 255, 35]`).
2. **Final SHA-256 == Baseline SHA-256** (`1bffe3dacafdd1ab180b78ca1e67c6c949bc214641b86c4f92d59ecd90367447`).
3. **Zero cross-subsystem contamination** (Game Mode, Remap L1/L2/L3, RGB Global, Macro, Hall, and DKS tables completely unchanged).
4. **Forbidden writes = 0** (only opcode `AA 24` was permitted; exactly 20 packets executed).

---

## 2. Canonical Wire Format Specification (AA 24 / 55 14)

The Type 84 HID wire protocol enforces a standard 64-byte report header structure across all chunked tables:

```
Byte 0     : Prefix (0xAA Host->Device, 0x55 Device->Host)
Byte 1     : Opcode (0x24 for write, 0x14 for read)
Byte 2     : Chunk size (0x38 for full chunks, 0x08 for tail chunk)
Bytes 3..4 : Address uint16_le (0x0000, 0x0038, ..., 0x01F8)
Byte 5     : Sub-header / reserved (0x00)
Byte 6     : Tail flag (0x01 on chunk #9, 0x00 on chunks #0..#8)
Byte 7     : Sub-header / reserved (0x00)
Bytes 8..63: PAYLOAD (56 bytes on chunks #0..#8, 8 bytes on tail chunk #9)
```

### Slot Memory Mapping
- Total buffer size: 512 bytes (128 slots × 4 bytes).
- Physical keys: 84 LEDs mapped across logical slots 0..86.
- Wire slot format: `[Slot_ID, R, G, B]` (vendor JS: `l[m] = i, l[m+1] = red, l[m+2] = green, l[m+3] = blue`).

---

## 3. Code Modifications (BUG-1 / BUG-2 / BUG-3)

### 1. `src/keyboard_re/protocol/read.py` (BUG-1)
- In `parse_rgb_per_key_read_chunks`:
  - Full chunks (0..8): changed payload extraction from `resp[5 : 5 + 56]` to `resp[8 : 8 + 56]`.
  - Tail chunk (9): changed payload extraction from `tail[5 : 5 + 8]` to `tail[8 : 8 + 8]`.

### 2. `src/keyboard_re/protocol/rgb.py` (BUG-2)
- In `build_rgb_per_key_chunks`:
  - Chunks 0..8: assemble full 64-byte report with prefix `AA 24 38`, address at bytes 3..4, and payload at `pkt[8 : 8 + len(payload)]`.
  - Tail chunk (9): assemble 64-byte report with prefix `AA 24 08`, address `504` (`0x01F8`), `pkt[6] = 0x01` (`isLastPacket`), and tail payload at `pkt[8 : 8 + len(tail_payload)]`.
- In `parse_rgb_per_key_chunks`:
  - Chunks 0..8: extract payload from `rep[8 : 8 + RGB_PER_KEY_CHUNK_PAYLOAD_SIZE]`.
  - Tail chunk (9): extract payload from `tail_rep[8 : 8 + RGB_PER_KEY_TAIL_PAYLOAD_SIZE]`.

### 3. `src/keyboard_re/protocol/transport.py` (BUG-3)
- In `MockHidTransport.send_report`:
  - Echo sub-headers and flags: `ack[5:8] = data[5:8]`.
  - Mirror payload starting at offset 8:
    ```python
    sz = data[2]
    if 0 < sz <= (REPORT_SIZE - 8) and len(data) >= 8 + sz:
        ack[8 : 8 + sz] = data[8 : 8 + sz]
    ```

---

## 4. Test Suite Alignment & Verification

1. **`tests/test_read_protocol.py`**:
   - Updated `test_parse_rgb_per_key_read_chunks_golden` to expect slot 126 as `bytes([126, 0, 0, 0])` instead of the former offset-5 subheader artifact `b"\x00\x01\x00"`.
2. **`tests/unit/test_ui_per_key_rgb.py`**:
   - In `test_1_led_id_byte_exact_preservation_across_all_mutations`, ensured canonical `RGBMatrix` is loaded on `working_profile` before testing ID preservation across mutations.
3. **`tests/test_hid_offset_regression.py`**:
   - All 23 dedicated offset boundary tests pass cleanly.
4. **Full Suite Execution**:
   ```
   ======================= 459 passed, 6 skipped in 4.69s ========================
   ```

---

## 5. Physical Hardware Closed-Loop ABA Validation Telemetry

Execution script: `scratch/run_physical_per_key_rgb_aba.py`  
Connected device: Type 84 Magnetic Black (`0x0C45:0x80D6`) via `NativeHidTransport`.

### Phase 1: Preflight Baseline Collection
- Input buffer drained: 0 stale packets.
- Initial Subsystem Baselines:
  - Game Mode (`AA 11`): `00000001000600000000000100000100`
  - Remap L1 (`AA 12`): `8c0173ff74e3ca10109fd0c61c194b33dd5faf0171a174dd38c399bf72270d01`
  - Remap L2 (`AA 16`): `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23`
  - Remap L3 (`AA 1C`): `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23`
  - RGB Global (`AA 13`): `55131000000001000fffffff0000000001050400...`
  - Macro (`AA 15`): `7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5`
  - DKS (`AA 18`): `5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`
  - Hall P1 (`AA 17`): `4a7f0fd152910ed6a13afe17576d75a8041e6b9b1bd486ff9518fd719a541d09`
- Per-Key RGB Baseline (`AA 14`):
  - **Baseline SHA-256:** `1bffe3dacafdd1ab180b78ca1e67c6c949bc214641b86c4f92d59ecd90367447`
  - Target Slot 35 (Key 'W') initial bytes: `[35, 0, 0, 35]`

### Phase 2: State B Mutation & Physical Transmission
- Target Slot 35 mutated to Green: `[35, 0, 255, 35]` (offset 142 mutated `0x00` -> `0xFF`).
- Target SHA-256: `0dcc38c0dadea1f7b9f06e0bf271baaef2177fbf6031ad521fac73344e7046ab`.
- Transmitted 10 `AA 24` write chunks via `SafetyFilteredTransport`:
  ```
  Chunk #0 (addr=0x0000, sz=56) -> ACK 55 24 [OK]
  Chunk #1 (addr=0x0038, sz=56) -> ACK 55 24 [OK]
  Chunk #2 (addr=0x0070, sz=56) -> ACK 55 24 [OK]
  Chunk #3 (addr=0x00A8, sz=56) -> ACK 55 24 [OK]
  Chunk #4 (addr=0x00E0, sz=56) -> ACK 55 24 [OK]
  Chunk #5 (addr=0x0118, sz=56) -> ACK 55 24 [OK]
  Chunk #6 (addr=0x0150, sz=56) -> ACK 55 24 [OK]
  Chunk #7 (addr=0x0188, sz=56) -> ACK 55 24 [OK]
  Chunk #8 (addr=0x01C0, sz=56) -> ACK 55 24 [OK]
  Chunk #9 (addr=0x01F8, sz=8)  -> ACK 55 24 [OK]
  ```

### Phase 3: Closed-Loop Readback (State B)
- Per-Key RGB readback via `AA 14`:
  - Readback SHA-256: `0dcc38c0dadea1f7b9f06e0bf271baaef2177fbf6031ad521fac73344e7046ab`
  - Slot 35 readback bytes: `[35, 0, 255, 35]`
  - Byte diff across all 512 bytes: **exactly 0 differences from target**.
  - **Verdict:** TARGET READBACK EXACT.

### Phase 4: Baseline Restoration (State A) & Verification
- Transmitted 10 `AA 24` restore chunks with baseline buffer.
- ACKs received: 10 × `55 24` [OK].
- Final `AA 14` readback:
  - Final Readback SHA-256: `1bffe3dacafdd1ab180b78ca1e67c6c949bc214641b86c4f92d59ecd90367447`
  - Slot 35 final bytes: `[35, 0, 0, 35]`
  - Comparison with baseline: `final_sha == baseline_sha` (**100% IDENTICAL**).

### Phase 5: Cross-Subsystem Invariance & Safety Verification
- Re-queried all 8 other device subsystems:
  - Game Mode: UNCHANGED (`00000001000600000000000100000100`)
  - Remap L1: UNCHANGED (`8c0173ff74e3ca10109fd0c61c194b33dd5faf0171a174dd38c399bf72270d01`)
  - Remap L2: UNCHANGED (`3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23`)
  - Remap L3: UNCHANGED (`3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23`)
  - RGB Global: UNCHANGED (`55131000000001000fffffff0000000001050400...`)
  - Macro Table: UNCHANGED (`7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5`)
  - DKS Table: UNCHANGED (`5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`)
  - Hall Profile 1: UNCHANGED (`4a7f0fd152910ed6a13afe17576d75a8041e6b9b1bd486ff9518fd719a541d09`)
- Write Safety Metrics:
  - Allowed `AA 24` writes: 20 packets.
  - Forbidden writes attempted: **0**.

---

## 6. Subsystem Closure Matrix

| Subsystem | Read Opcode | Write Opcode | Wire Offset | Status |
|---|---|---|---|---|
| **Remap Layer 1 (Base)** | `AA 12` | `AA 22` | Offset 8 | **CLOSED** |
| **Remap Layer 2 (Fn)** | `AA 16` | `AA 26` | Offset 8 | **CLOSED** |
| **Default Fn (Layer 3)** | `AA 1C` | N/A | Offset 8 | **CLOSED** |
| **RGB Global** | `AA 13` | `AA 23` | Offset 8 | **CLOSED** |
| **RGB Per-Key** | `AA 14` | `AA 24` | **Offset 8 (Fixed BUG-1/2/3)** | **CLOSED** |
| **Hall Effect / Rapid Trigger** | `AA 17` | `AA 27` | Offset 8 | **CLOSED** |
| **Dynamic Keystroke (DKS)** | `AA 18` | `AA 28` | Offset 8 | **CLOSED** |
| **Game Mode** | `AA 11` | `AA 21` | Offset 8 | **CLOSED** |
| **Macro Engine** | `AA 15` | `AA 25` | Offset 8 | **CLOSED** |

---

**RGB PER-KEY OFFSET BUGS (BUG-1, BUG-2, BUG-3): CLOSED**
