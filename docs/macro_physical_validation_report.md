# Macro Subsystem (AA 15 / AA 25) Physical Validation & Architecture Report

**Target Hardware:** IO by Red Square Type 84 Magnetic Black (`0x0C45:0x80D6`)  
**Status:** PHYSICALLY VERIFIED (Software Production Ready / Hardware ABA Validated)  
**Date:** 2026-09-16  

---

## 1. Executive Summary

The **Macro** subsystem (`AA 15` read / `AA 25` write) has been reverse-engineered from vendor JS implementation (`Yo` / `Ou`), fully implemented across models, encoders, decoders, profile manager diffing, write planning, executor, and GUI editor (`MacrosView`), and physically validated on live hardware via a controlled read-only preflight and zero-risk ABA write/rollback transaction.

Key findings & achievements:
1. **Wire Architecture Confirmed:**
   - Catalog: Exactly 400 bytes at address `0x0000` (100 slots $\times$ 4-byte `uint32_le` heap pointers).
   - Heap: Begins at address `0x0190` (400).
   - Macro Body: 4-byte header `[f & 0xFF, (f >> 8) & 0xFF, 0x00, 0x00]` where $f = k \times 2$ ($k$ = action count), followed by $k \times 4$ bytes of action records `[delay uint16_le, keyCode uint8, flags uint8]`.
   - Flags: Bit 7 = `isPress` (1 = Press, 0 = Release), Bits 4..6 = `actionType` (1 = Keyboard, 3 = Mouse).
2. **Two-Stage Write Architecture (AA 25):**
   - Stage 1: Catalog written in 8 chunks (7 $\times$ 56B + 1 $\times$ 8B tail).
   - Stage 2: Heap written in 56-byte chunks (address $\ge 400$).
   - The `isLastPacket` flag (`pkt[6] = 0x01`) is asserted strictly on the final chunk of the entire sequence.
3. **Physical Hardware Validation (ABA):**
   - Preflight verified clean baseline catalog (`7a12e561...`) and confirmed `AA 15` heap read response format.
   - Target mutation (Slot 0 defined with 1 action, 8B heap) transmitted 9 `AA 25` chunks, received valid `55 25` ACKs on both catalog and heap stages.
   - Readback via `AA 15` confirmed exact slot pointer (`0x0190`) and heap payload byte-for-byte.
   - Rollback restored the clean baseline catalog; readback matched baseline SHA-256 bit-for-bit.
   - Cross-subsystem checks confirmed ZERO side-effects on Remap L1 (`AA 12`), Remap L2 (`AA 16`), DKS (`AA 18`), Hall RT (`AA 17`), and RGB Global (`AA 13`).
   - Forbidden writes count: strictly 0.

---

## 2. Protocol Specification (AA 15 / AA 25)

### Read Sequence (AA 15)

- **Catalog Read:** 8 requests issued sequentially:
  - 7 chunks of 56 bytes at addresses `0, 56, 112, 168, 224, 280, 336`. Request: `AA 15 38 [addr_lo] [addr_hi] 00 00 00`.
  - 1 tail chunk of 8 bytes at address `392` (`0x0188`). Request: `AA 15 08 88 01 00 01 00`.
- **Heap Read:** Arbitrary offsets $\ge 400$:
  - Request: `AA 15 [size] [addr_lo] [addr_hi] 00 00 00`.
  - Response: `55 15 [size] [addr_lo] [addr_hi] 00 00 00` + `[size]` bytes of payload at report offset 8 (`resp[8 : 8 + size]`).

### Write Sequence (AA 25)

- No separate commit opcode is required.
- **Stage 1 (Catalog):**
  - Chunks 0..6: Address `i * 56`, size 56, `isLastPacket = 0x00`.
  - Chunk 7: Address 392, size 8. If heap length $m = 0$, `isLastPacket = 0x01`; if heap length $m > 0$, `isLastPacket = 0x00`.
- **Stage 2 (Heap, if $m > 0$):**
  - Heap chunks start at address `400` (`0x0190`) in slices of up to 56 bytes.
  - The final heap chunk sets `isLastPacket = 0x01`.
- **ACK Format:**
  - `55 25 [size] [addr_lo] [addr_hi] [00] [is_last] [00] ...`

---

## 3. Physical Preflight Telemetry

Executed via [run_physical_macro_preflight.py](file:///c:/KeyboardSoft/scratch/run_physical_macro_preflight.py) using `ReadOnlySafetyTransport`:

| Buffer / Subsystem | Opcode | Size | SHA-256 Hash | Preflight Status |
|---|---|---|---|---|
| **Macro Catalog** | `AA 15` | 400B | `7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5` | 100 slots all `0x00000000` |
| **Remap Layer 1** | `AA 12` | 512B | `8c0173ff74e3ca10109fd0c61c194b33dd5faf0171a174dd38c399bf72270d01` | Isolation baseline |
| **Remap Layer 2** | `AA 16` | 512B | `3b9b2717200424ba04ed1526b2acf0754ffacf4f7415a5d507e8c9b7f84fea23` | Isolation baseline |
| **DKS Table** | `AA 18` | 1024B | `5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef` | Isolation baseline |
| **Hall RT P1** | `AA 17` | 1008B | `7f9e430cbd8fb89042c4ef94f0ef2f00b60896d958e80cfe5214715690bca2ca` | Isolation baseline |
| **RGB Global** | `AA 13` | 64B | Effect `0x0F` (Ripple Spread) | Isolation baseline |

### Heap Probe at Address 400 (0x0190)
- 4 bytes read: `55 15 04 90 01 00 00 00 00 00 00 00` -> payload `00 00 00 00`.
- 12 bytes read: `55 15 0C 90 01 00 00 00 00 00 00 00 ...` -> payload `00 00 00 00 00 00 00 00 00 00 00 00`.

---

## 4. Controlled Physical ABA Test Results

Executed via [run_physical_macro_aba_test.py](file:///c:/KeyboardSoft/scratch/run_physical_macro_aba_test.py) using `SafetyFilteredTransport`:
- **Allowed Write Opcode:** STRICTLY `AA 25` ONLY.
- **Forbidden Attempts:** 0.

### Stage A -> B: Target Write & Validation
1. **Target Payload:**
   - Slot 0 pointer: `0x00000190` (400).
   - Slot 0 macro body: Key 'A' (0x04), Press, delay 50ms.
   - Body bytes: `02 00 00 00 32 00 04 90` (8 bytes).
2. **Transmission (9 Chunks):**
   - Chunks 0..6 (addr 0..336, 56B): ACK `55 25 38` OK.
   - Chunk 7 (addr 392, 8B tail, `last=0`): ACK `55 25 08` OK.
   - Chunk 8 (addr 400, 8B heap, `last=1`): ACK `55 25 08` OK.
3. **Readback Verification via AA 15:**
   - Slot 0 pointer readback: `0x0190` (MATCH).
   - Heap bytes readback at 400: `02 00 00 00 32 00 04 90` (MATCH).
   - Slots 1..99: All `0x00000000` (MATCH).

### Stage B -> A': Rollback to Baseline
1. **Transmission (8 Chunks):**
   - Chunks 0..6 (addr 0..336, 56B): ACK `55 25 38` OK.
   - Chunk 7 (addr 392, 8B tail, `last=1`): ACK `55 25 08` OK.
2. **Readback Verification via AA 15:**
   - Catalog SHA-256: `7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5` (EXACT MATCH with preflight baseline).
3. **Cross-Subsystem Verification:**
   - Remap L1 (`AA 12`): SHA MATCH.
   - Remap L2 (`AA 16`): SHA MATCH.
   - DKS Table (`AA 18`): SHA MATCH.
   - Hall RT (`AA 17`): SHA MATCH.
   - RGB Global (`AA 13`): Config MATCH.

---

## 5. Known Substantial Limitations & Boundaries

1. **Host-Side Name Storage:**
   The keyboard firmware flash stores only the binary action sequence (delays, keycodes, press/release flags). Macro names (e.g. "Quick Fire", "Jump") are host-side metadata saved in profile JSON files (`Profile.metadata["macro_names"]`).
2. **Flash Boundary Safety:**
   In this investigation, we strictly verified macro heap allocations starting at address 400 without stress-testing arbitrary upper boundaries. For safety, individual user macros are capped within reasonable event sizes in the UI.
3. **Keymap Macro Binding:**
   To trigger a macro from a physical key, the key in Remap Layer 1 (`AA 22`) or Layer 2 (`AA 26`) must be configured with `action_type = 0x05`, where the payload specifies the macro slot ID.
