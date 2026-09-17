# Game Mode / Performance Settings (AA 11 / AA 21) Closure Report

**Target Hardware:** IO by Red Square Type 84 Magnetic Black (`0x0C45:0x80D6`)  
**Status:** CLOSED  
**Date:** 2026-09-16  

---

## 1. Executive Summary

The **Game Mode / Performance Settings** subsystem (`AA 11` read / `AA 21` write) has been completely reverse-engineered, implemented in production code, integrated into the user interface, verified with unit/controller/protocol tests, and physically validated on real hardware through a controlled ABA write/rollback cycle.

Key achievements:
1. **Full Protocol Implementation:** `AA 21` single-report write with `55 21` ACK validation adheres strictly to the existing pipeline architecture (`build_chunk_packet`, `validate_chunk_ack`, `ProfileWritePlan`, `ProfilePlanExecutor`, and `AppController`).
2. **Comprehensive Field Support:** All 13 vendor fields are decoded and serialized cleanly without data loss.
3. **Aesthetic & Seamless UI:** Added `SettingsView` in `MainWindow` with intuitive controls for polling rate (1000/4000/8000 Hz), hardware stability mode, auto-calibration, Win key lock, Fn switch, sleep timeout, key debounce delay, analog deadzones, and OS system mode.
4. **Physical ABA Test Passed 100%:** Hardware write to `stabilityMode` succeeded, produced valid `55 21` ACK, readback confirmed exact 1-byte target diff, and rollback restored the baseline state bit-for-bit.
5. **Zero Isolation Bleed:** Remap L1 (`AA 12`), Remap L2 (`AA 16`), DKS (`AA 18`), RGB Global/Matrix, and Hall switch configurations were completely untouched.

---

## 2. Wire Protocol Architecture (AA 11 / AA 21)

| Parameter | Specification |
|---|---|
| **Read Opcode** | `AA 11 38 00 00 00 01 00` (HOST -> DEVICE), response `55 11 38 00 00 00 01 00` + 56B payload |
| **Write Opcode** | `AA 21 38 00 00 00 01 00` + 56B payload (HOST -> DEVICE) |
| **Expected ACK** | `55 21 38 00 00 00 01 00` + 56B payload echo (DEVICE -> HOST) |
| **Report Size** | Exactly 64 bytes (8B header + 56B payload) |
| **Packet Sequence** | 1 report (address `0`, size `56`, `isLastPacket = 0x01`) |
| **Payload Size** | 56 bytes |

### Payload Byte Offsets

| Payload Offset | Report Offset | Field Name | Type / Values | Description |
|---|---|---|---|---|
| `[1]` | `[9]` | `gameMode` | `uint8` (0/1) | Game Mode / Windows key lock |
| `[2]` | `[10]` | `fnSwitch` | `uint8` (0/1) | Fn default layer swap |
| `[3]` | `[11]` | `sleepTime` | `uint8` (0..30) | Sleep timeout in minutes |
| `[4]` | `[12]` | `keyDelay` | `uint8` (0..10) | Debounce delay in ms |
| `[5]` | `[13]` | `reportRate` | `uint8` (3, 5, 6) | Polling rate (3=1000Hz, 5=4000Hz, 6=8000Hz) |
| `[6]` | `[14]` | `systemMode` | `uint8` (0/1) | OS mode (0=Windows, 1=Mac) |
| `[7]` | `[15]` | `tftDisplayTime` | `uint8` | Screen timeout |
| `[8]` | `[16]` | `topDeadZone` | `uint8` (mm × 100) | Top analog deadzone |
| `[9]` | `[17]` | `bottomDeadZone` | `uint8` (mm × 100) | Bottom analog deadzone |
| `[11]` | `[19]` | `stabilityMode` | `uint8` (0/1) | Hall sensor hardware filter |
| `[14]` | `[22]` | `autoCalibration` | `uint8` (0/1) | Magnetic dynamic self-calibration |
| `[15]` | `[23]` | `singleKeyWakeup` | `uint8` (0/1) | Wakeup from single key press |
| `[16]` | `[24]` | `pushButtonMode` | `uint8` (0/1) | Push button mode |

---

## 3. UI Implementation

Integrated in [settings_view.py](file:///c:/KeyboardSoft/src/keyboard_re/ui/views/settings_view.py) and registered as the `"Settings"` tab in [main_window.py](file:///c:/KeyboardSoft/src/keyboard_re/ui/views/main_window.py):
- **Polling Rate:** `CTkSegmentedButton` with only valid vendor options: `1000 Hz`, `4000 Hz`, `8000 Hz`.
- **Switches:** Clean toggles for `Stability Mode`, `Auto Calibration`, `Game Mode (Lock Windows Key)`, and `Fn Switch`.
- **Sliders:** 
  - Sleep Timeout: 0 to 30 minutes.
  - Debounce Delay: 0 to 10 ms.
  - Analog Deadzones: Top / Bottom deadzone sliders (0.00 to 0.50 mm).
- **OS Layout:** Windows / Mac mode toggle.
- **Reset:** "Reset Settings to Device Defaults" restores baseline values from connected device state.
- **Workflow:** Modifying settings marks working profile as dirty, recalculates diff, updates confirmation summary (queued step `game_mode`, opcode `0x21`), and executes dry-run or real apply cleanly.

---

## 4. Physical Preflight Telemetry

Connection via `NativeHidTransport` wrapped with `SafetyFilteredReadOnlyTransport` (zero writes permitted):

| Buffer / Subsystem | Opcode | SHA-256 Hash | Size | Preflight Status |
|---|---|---|---|---|
| **Game Mode** | `AA 11` | `fa06bd4037a3be8576bc3e1bf58a1bd95011e5e9bf8f2c896e4d2bf8aa60b1d1` | 56B | Baseline recorded |
| **Remap Layer 1** | `AA 12` | `4f0d062657b34f43059d579b5615a113d3958fffb1bd86e44cb23a4c1cb53b6d` | 512B | Isolation baseline |
| **Remap Layer 2** | `AA 16` | `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` | 512B | Isolation baseline |
| **DKS Table** | `AA 18` | `1db1309ef023cd3ba0956cc422f4625b82a897851846e69b4364d276126b91e1` | 1024B | Isolation baseline |

### Physical Baseline Values
- `sleep_time`: 1 min
- `report_rate`: 6 (8000 Hz)
- `stability_mode`: 1 (enabled)
- `auto_calibration`: 1 (enabled)
- `game_mode`: 0 (disabled)
- `fn_switch`: 0 (disabled)
- `key_delay`: 0 ms
- `system_mode`: 0 (Windows)
- `top_deadzone`: 0.00 mm
- `bottom_deadzone`: 0.00 mm

---

## 5. Controlled Physical ABA Test

Executed via [run_physical_game_mode_aba_test.py](file:///c:/KeyboardSoft/scratch/run_physical_game_mode_aba_test.py) using `SafetyFilteredTransport`:
- **Allowed Write Opcode:** `AA 21` ONLY.
- **Forbidden Writes:** Strictly 0 attempts permitted.
- **Safe Field Mutated:** `stability_mode` (1 -> 0 -> 1).
- **Modified Byte:** Payload offset 11 (`0x01` -> `0x00`).

### Telemetry & Hashes

| Phase | Operation | Payload SHA-256 | Verification Result |
|---|---|---|---|
| **A (Baseline)** | Read `AA 11` | `fa06bd4037a3be8576bc3e1bf58a1bd95011e5e9bf8f2c896e4d2bf8aa60b1d1` | Matches preflight SHA |
| **B (Target Write)** | Write `AA 21` | `7a262f9930de3c03d26edd2db7201a78b01f6e7e390a1b2e217553f8fec5ca2b` | ACK `55 21` received |
| **B (Readback)** | Read `AA 11` | `7a262f9930de3c03d26edd2db7201a78b01f6e7e390a1b2e217553f8fec5ca2b` | **Exact target match** |
| **A (Restore Write)** | Write `AA 21` | `fa06bd4037a3be8576bc3e1bf58a1bd95011e5e9bf8f2c896e4d2bf8aa60b1d1` | ACK `55 21` received |
| **A (Final Readback)**| Read `AA 11` | `fa06bd4037a3be8576bc3e1bf58a1bd95011e5e9bf8f2c896e4d2bf8aa60b1d1` | **Final SHA == Baseline SHA** |

### Verification Details
- **ACK Count:** 2 writes executed (`AA 21`), 2 valid `55 21` ACKs received.
- **Diff Baseline -> Target:** Exactly 1 byte changed across the 56-byte payload (offset `+11`: `0x01 -> 0x00`).
- **Diff Baseline -> Final:** 0 bytes difference (100% bit-exact restoration).
- **Forbidden Writes:** 0.
- **Remap L1 (`AA 12`):** SHA unchanged (`4f0d0626...`).
- **Remap L2 (`AA 16`):** SHA unchanged (`3b9b2717...`).
- **DKS (`AA 18`):** SHA unchanged (`1db1309e...`).
- **RGB / Hall:** Unaffected.

---

## 6. Software & Regression Test Results

### Full Pytest Suite
```
tests/unit/test_game_mode.py::TestGameModeProtocol (6 passed)
tests/unit/test_game_mode.py::TestGameModeStateAndDiff (6 passed)
tests/unit/test_game_mode.py::TestGameModeAppController (4 passed)
...
======================= 402 passed, 6 skipped in 3.18s ========================
```

### UI Smoke Tests
- `smoke_test_settings_ui.py`: All 10 stages passed (connection, render, segmented polling rate, switches, sliders, write plan generation, mock apply).
- `smoke_test_remap_ui.py`: All 10 stages passed (L1/L2 switching, read-only protection, remap modification, mock apply).
- `smoke_test_dks_ui.py`: All 9 stages passed (DKS allocation, travel point adjustments, event toggling, mock apply).

---

## 7. Status Conclusion

Every requirement has been fully met:
- Wire format reverse-engineering and packet framing: Verified.
- Software abstractions (Plan, Diff, Executor, Controller, UI): Verified.
- Automated tests: 402 passing.
- Physical preflight and ABA validation: 100% success with zero forbidden writes and zero persistent residual changes.

**GAME MODE / SETTINGS: CLOSED**
