# Remap Layer 2 / Fn (AA 16 / AA 26) Closure Report

**Target Hardware:** IO by Red Square Type 84 Magnetic Black (`0x0C45:0x80D6`)  
**Status:** CLOSED  
**Date:** 2026-09-16  

---

## 1. Executive Summary

Subsystem **Remap Layer 2 / Fn** (`AA 16` read / `AA 26` write) has been fully brought to production, wired to the user interface, verified against vendor safety specifications, and confirmed via physical hardware closed-loop write/readback (ABA) testing on real device hardware.

Key achievements:
1. **Zero UI Duplication:** The existing visual keyboard canvas is shared dynamically between **Base Layer (L1)** and **Fn Layer (L2)** through a sleek segmented button in `RemapView`.
2. **Vendor Safety Lock Enforced:** Keys F1..F12 (`FN_DISABLED_SWITCH_SLOTS = 1..12`) and the physical Fn key (`FN_REMAP_SLOT = 85`) are strictly locked as read-only in Layer 2, preventing accidental loss of Fn functionality or media keys.
3. **Wire Format Verified:** Physical write (`AA 26`) and read (`AA 16`) match the 512-byte matrix chunking scheme (10 packets: 9 × 56B + 1 × 8B tail with `isLastPacket = 0x01`).
4. **Physical ABA Test Passed 100%:** Safe key remap mutation on Layer 2 was executed, verified exact byte-for-byte, confirmed with zero side-effects on Layer 1 (`AA 12`) and Default Fn (`AA 1C`), and restored back to exact baseline.

---

## 2. Wire Protocol Architecture (AA 16 / AA 26)

| Parameter | Specification |
|---|---|
| **Read Opcode** | `AA 16` (HOST -> DEVICE), response `55 16` (DEVICE -> HOST) |
| **Write Opcode** | `AA 26` (HOST -> DEVICE), ACK `55 26` (DEVICE -> HOST) |
| **Image Size** | Exactly 512 bytes (128 slots × 4 bytes per slot) |
| **Packet Sequence** | 10 sequential 64-byte HID reports |
| **Chunk Sizes** | Chunks 0..8: 56 bytes (`size=0x38`)<br>Chunk 9 (tail): 8 bytes (`size=0x08`, address `504` / `0x01F8`) |
| **Tail Marker** | Chunk 9 sets `pkt[6] = 0x01` (`isLastPacket`) |
| **ACK Verification** | Synchronous validation of prefix `0x55`, opcode `0x26`, size, address, and payload echo |

---

## 3. UI Implementation & Safety Rules

### Layer Switching
- In [remap_view.py](file:///c:/KeyboardSoft/src/keyboard_re/ui/views/remap_view.py), a segmented control allows switching between:
  - `Base Layer (L1)`
  - `Fn Layer (L2)`
- Switching updates `controller.set_active_remap_layer(layer)` without recreating UI widgets. Key cards, diff counters, and inspector badges automatically bind to the selected layer.

### Safety Lock (Vendor Rule)
- Vendor configuration specifies:
  ```javascript
  customKeysConfig.fnDisabledKeyIds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
  ```
- Implemented in [layout_data.py](file:///c:/KeyboardSoft/src/keyboard_re/ui/layout_data.py):
  `FN_DISABLED_SWITCH_SLOTS = frozenset(range(1, 13))` (F1..F12)
  `FN_REMAP_SLOT = 85` (Physical Fn key)
- When `active_remap_layer == 2`:
  - `info.is_readonly = True`
  - `set_key_binding(...)` and `unbind_key(...)` return `False`
  - UI displays `🔒 Fn Layer Protected (F1..F12 media lock)` or `🔒 System Reserved (Fn Key locked)`
  - Assign, Unbind, and Reset buttons are disabled in the UI for these keys.

---

## 4. Physical Preflight Telemetry

Physical connection established via `NativeHidTransport` with `SafetyFilteredReadOnlyTransport`:

| Target Table | Opcode | SHA-256 Hash | Size | Invariant Check |
|---|---|---|---|---|
| **Layer 2 (L2)** | `AA 16` | `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` | 512B | Baseline L2 |
| **Default Fn** | `AA 1C` | `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` | 512B | **AA 16 == AA 1C (100% IDENTICAL)** |
| **Layer 1 (L1)** | `AA 12` | `4f0d062657b34f43059d579b5615a113d3958fffb1bd86e44cb23a4c1cb53b6d` | 512B | Baseline L1 |

---

## 5. Controlled Physical ABA Test

Executed via [run_physical_remap_l2_aba_test.py](file:///c:/KeyboardSoft/scratch/run_physical_remap_l2_aba_test.py) using `SafetyFilteredTransport`:
- **Allowed Write Opcode:** `AA 26` ONLY.
- **Forbidden Writes:** Any other opcode blocked immediately.
- **Target Slot:** Switch slot 34 (Key 'W', remap slot 34, offset 136..139).
- **Baseline Record:** `02 00 1A 00` (HID 'W')
- **Target Record:** `02 00 2C 00` (HID 'Space')

### Test Execution Phases & Telemetry

1. **Target Write (AA 26):**
   - Transmitted 10 chunks to hardware.
   - ACKs received: 10 × `55 26` (all valid).
   - Target SHA-256: `23a718ed451102c83630eff405c237935b26cffc8375f66c6063a4bd7b5409cc`.

2. **Readback & Isolation Verification:**
   - Read L2 via `AA 16`: exact SHA match `23a718ed451102c83630eff405c237935b26cffc8375f66c6063a4bd7b5409cc`.
   - Byte diff against baseline: **exactly 1 byte modified** (offset `+138`: `0x1A` -> `0x2C`).
   - Read L1 via `AA 12`: SHA `4f0d062657b34f43059d579b5615a113d3958fffb1bd86e44cb23a4c1cb53b6d` (**100% UNTOUCHED**).
   - Read Default Fn via `AA 1C`: SHA `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` (**100% UNTOUCHED**).

3. **Rollback Write (AA 26):**
   - Transmitted 10 chunks with original baseline image.
   - ACKs received: 10 × `55 26` (all valid).

4. **Final Verification Check:**
   - Final L2 SHA: `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` (**== Baseline L2**).
   - Final L1 SHA: `4f0d062657b34f43059d579b5615a113d3958fffb1bd86e44cb23a4c1cb53b6d` (**== Baseline L1**).
   - Final AA 1C SHA: `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` (**== Baseline Def Fn**).
   - Total forbidden writes: **0**.
   - Total `AA 26` packets sent: **20** (10 target + 10 rollback).
   - Total `55 26` ACKs received: **20**.

---

## 6. Regression & Quality Assurance

- **Unit & Protocol Tests:**
  - `pytest tests/unit/test_remap_l2.py -v`: 8 passed.
  - `pytest tests/unit/test_ui_remap.py -v`: 18 passed.
  - `pytest tests/test_remap_write_pipeline.py -v`: 20 passed.
  - `pytest tests/test_profile_executor.py -v`: 15 passed.
- **Full Test Suite:**
  - `pytest`: **386 passed, 6 skipped** (0 errors, 0 failures).
- **Headless UI Smoke Test:**
  - `python scratch/smoke_test_remap_ui.py`: All 10 verification steps passed.
  - `python scratch/smoke_test_dks_ui.py`: All 9 verification steps passed.

---

## 7. Status of Subsystems

| Subsystem | Read Opcode | Write Opcode | Status |
|---|---|---|---|
| **Remap Layer 1 (Base)** | `AA 12` | `AA 22` | **CLOSED** |
| **Remap Layer 2 (Fn)** | `AA 16` | `AA 26` | **CLOSED** |
| **Default Fn (Layer 3)** | `AA 1C` | N/A (Firmware ROM / Reference) | **CLOSED** |
| **RGB Global** | `AA 13` | `AA 23` | **CLOSED** |
| **RGB Per-Key** | `AA 14` | `AA 24` | **CLOSED** |
| **Hall Effect / Rapid Trigger** | `AA 17` | `AA 27` | **CLOSED** |
| **Dynamic Keystroke (DKS)** | `AA 18` | `AA 28` | **CLOSED** |
| **Game Mode** | `AA 11` | `AA 21` | *Next Module* |

---

**REMAP L2 / FN: CLOSED**
