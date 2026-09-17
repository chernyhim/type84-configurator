# Hall Effect / Rapid Trigger Subsystem Closure Report

## Subsystem Status: CLOSED

- **Subsystem**: Hall Effect / Rapid Trigger Analog Matrix
- **Hardware Target**: IO by Red Square Type 84 Magnetic Black (VID `0x0C45`, PID `0x80D6`)
- **Protocol Opcodes**: `AA 17` (read) / `AA 27` (write)
- **Profile Support**: Dual profile slots (`profile=1` default, `profile=2`)
- **Verification Status**: **100% PHYSICALLY VERIFIED ON REAL HARDWARE (CLOSED-LOOP ABA PASS)**

---

## 1. Protocol & Wire Architecture

### Wire Format
Each physical switch record is strictly **8 bytes** packed as little-endian `<BBHHH`:

| Offset | Field | Type | Unit / Description |
|---|---|---|---|
| `+0` | `axis_type` | `uint8` | Switch axis type (`0` or `1`) |
| `+1` | `flags` | `uint8` | Bit 0: `isWholeFast` / Rapid Trigger enabled (`1=ON`, `0=OFF`). Bit 1: `isRampageMode` |
| `+2..3` | `actuation` | `uint16 LE` | Actuation threshold (1 unit = 0.01 mm step, e.g. 120 = 1.20 mm) |
| `+4..5` | `rt_press` | `uint16 LE` | Rapid Trigger press sensitivity (1 unit = 0.01 mm, e.g. 20 = 0.20 mm) |
| `+6..7` | `rt_release` | `uint16 LE` | Rapid Trigger release sensitivity (1 unit = 0.01 mm, e.g. 20 = 0.20 mm) |

### Physical Matrix Addressing
Addressing is dense across banks and columns with zero bank preamble:
$$\text{Address} = (\text{bank} \times 16 + \text{column}) \times 8$$

* 8 banks $\times$ 16 columns = 128 candidate slots (1024 bytes total addressing space).
* 84 physical keys mapped across Banks 0..6.
* Full image size: 1008 bytes (18 chunks of 56 bytes) + 1 terminator report (16 bytes at address 1008 / `0x03F0`).

### Packet Framing
* **Read Request (`AA 17`)**: 18 requests of `AA 17 38 <addr_lo> <addr_hi> ...` + 1 terminator request `AA 17 10 F0 03 00 01 00 ...`.
* **Read Response (`55 17`)**: 8-byte header `55 17 38 <addr_lo> <addr_hi> 00 <flags> 00` + 56 bytes payload (`resp[8:64]`).
* **Write Packet (`AA 27`)**: 18 output reports of `AA 27 38 <addr_lo> <addr_hi> 00 00 00 <payload...>` + 1 commit terminator `AA 27 10 F0 03 00 01 00 ...`.
* **Write ACK (`55 27`)**: Device echoes `55 27 38 <addr_lo> <addr_hi> 00 00 00 <echo...>`.

---

## 2. Physical Read-Only Preflight

Executed against physical keyboard:
- **Baseline SHA-256**: `4a7f0fd152910ed6a13afe17576d75a8041e6b9b1bd486ff9518fd719a541d09`
- **Known Keys Decoded**:
  - Key `A` @ `0x0188`: `00 00 78 00 00 00 00 00` (Actuation 1.20mm, RT OFF)
  - Key `S` @ `0x0190`: `00 00 78 00 00 00 00 00` (Actuation 1.20mm, RT OFF)
  - Key `D` @ `0x0198`: `00 00 78 00 00 00 00 00` (Actuation 1.20mm, RT OFF)
  - Key `SPACE` @ `0x0298`: `00 00 78 00 00 00 00 00` (Actuation 1.20mm, RT OFF)
- **Subsystem Non-Interference Baselines**:
  - Remap L1 (`AA 12`): `8c0173ff74e3ca10109fd0c61c194b33dd5faf0171a174dd38c399bf72270d01`
  - Remap L2 (`AA 16`): `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23`
  - DKS (`AA 18`): `5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`
  - Game Mode (`AA 11`): `1a0aa657a0ce140c7cee95a54f55b0f93f85867bbf909352e7c1796e850960e5`

---

## 3. Physical Closed-Loop ABA Test Results

* **Safety Filter**: `SafetyFilteredTransport` allowing strictly write opcode `AA 27`. Forbidden attempts: **0**.
* **Target Key**: Key `S` (slot 50, address `400 = 0x0190`).

### Step A: RT OFF → RT ON
1. Prepared target payload: `00 01 78 00 14 00 14 00` (RT Enabled: `flags=0x01`, `actuation=1.20mm`, `rt_press=0.20mm`, `rt_release=0.20mm`).
2. Transmitted 19 write chunks via `AA 27`.
3. All 19 chunks acknowledged with valid `55 27` ACKs matching chunk addresses.
4. Readback via `AA 17`:
   - Key `S` readback: `00 01 78 00 14 00 14 00` (exact match).
   - Official vendor JS decoder evaluation (`https://web.io.vision/` function `Xo`):
     - `isWholeFast === true`
     - `triggerKeyStroke === 1.20 mm`
     - `pressRT === 0.20 mm`
     - `releaseRT === 0.20 mm`
   - Changed byte count in 1008-byte image: exactly 3 (`flags`, `rt_press`, `rt_release`). Zero collateral modifications.
5. User inspection window: 15-second holding pause for live web configurator inspection.

### Step B: RT ON → RT OFF (Restore)
1. Transmitted baseline payload (Key `S` restored to `00 00 78 00 00 00 00 00`).
2. All 19 restore chunks acknowledged with valid `55 27` ACKs.
3. Final readback SHA-256: `4a7f0fd152910ed6a13afe17576d75a8041e6b9b1bd486ff9518fd719a541d09` (100% byte-for-byte baseline match).

### Non-Interference Verification
All 4 other subsystems re-read and verified unchanged:
* Remap L1 (AA 12): UNCHANGED (SHA matched)
* Remap L2 (AA 16): UNCHANGED (SHA matched)
* DKS (AA 18): UNCHANGED (SHA matched)
* Game Mode (AA 11): UNCHANGED (SHA matched)

### Safety Verification
* Forbidden write attempts: **0**
* Total write packets sent: **38** (`AA 27`, 19 ON + 19 OFF)

---

## Conclusion
The Hall Effect & Rapid Trigger subsystem is fully aligned with vendor hardware behavior and firmware specifications, and is completely **CLOSED**.
