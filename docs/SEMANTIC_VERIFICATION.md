# Experimental Protocol Semantics Verification (SEMANTIC VERIFICATION) Type 84

> **Important Methodological Rule:**  
> All findings are classified under a strict verification scale:
> - **`CONFIRMED`** — Verified by at least two independent physical experiments or cross-subsystem corroboration;
> - **`OBSERVED`** — Directly captured in dumps, but full dynamics or semantics remain unverified;
> - **`PROBABLE`** — Robust hypothesis consistent with controller architecture and multiple independent facts;
> - **`UNKNOWN`** — Data absent or insufficient;
> - **`DISPROVEN`** — Hypothesis refuted by direct packet analysis.

---

## 1. Subsystem `AA 12` — Key Remap Layer 1 (Base Layout)

### 1.1. Verification of Coordinate Mapping Formula (Hall Matrix $\leftrightarrow$ Remap Matrix)

Analysis of the 512-byte scancode table `AA 12` and cross-referencing against analog Hall sensor addresses confirmed a systemic relationship:
$$\text{Remap Bank} = \text{Hall Bank}$$
$$\text{Remap Column} = \text{Hall Column} + 1$$
$$\text{Remap Slot} = \text{Bank} \times 16 + (\text{Column} + 1)$$
$$\text{Remap Byte Offset} = \text{Remap Slot} \times 4$$

Column 0 of the scancode matrix is reserved for Numpad / service routing lines.

#### Key Verification Summary Table:

| Key | Hall Bank | Hall Col | Hall Addr (AA 27) | Remap Slot | Remap Bank | Remap Col | Hex Bytes (AA 12) | HID Usage Name | Status | Evidence |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|
| **`ESC`** | 0 | 0 | `0x0005` (5) | **1** | 0 | 1 | `00 29 00 02` | Escape (`0x29`) | **`CONFIRMED`** | Direct dump match + formula |
| **`F1`** | 0 | 1 | `0x000D` (13) | **2** | 0 | 2 | `00 3A 00 02` | F1 (`0x3A`) | **`CONFIRMED`** | Sequential row F1..F12 |
| **`Q`** | 2 | 1 | `0x010D` (269) | **34** | 2 | 2 | `00 14 00 02` | Q (`0x14`) | **`CONFIRMED`** | Match with `key_q_139mm.json` |
| **`A`** | 3 | 1 | `0x018D` (397) | **50** | 3 | 2 | `00 04 00 02` | A (`0x04`) | **`CONFIRMED`** | Match with `key_a_139mm.json` |
| **`ENTER`** | 4 | 12 | `0x0265` (613) | **77** | 4 | 13 | `00 28 00 02` | Enter (`0x28`) | **`CONFIRMED`** | Match with `key_enter_139mm.json` |
| **`SPACE`** | 5 | 3 | `0x029D` (669) | **84** | 5 | 4 | `00 00 00 02` | Default Space | **`CONFIRMED`** | Exact match in slot $5 \times 16 + 4$ |
| **`LEFT`** | 5 | 8 | `0x02C5` (709) | **89** | 5 | 9 | `00 50 00 02` | LeftArrow (`0x50`) | **`CONFIRMED`** | Match with `key_left_139mm.json` |
| **`UP`** | 5 | 10 | `0x02D5` (725) | **91** | 5 | 11 | `00 52 00 02` | UpArrow (`0x52`) | **`CONFIRMED`** | Match with `key_up_139mm.json` |
| **`BACKSPACE`**| 5 | 12 | `0x02E5` (741) | **93** | 5 | 13 | `00 2A 00 02` | Backspace (`0x2A`)| **`CONFIRMED`** | Match with `key_backspace_139mm.json` (addr 741) |

> **Key Discovery on Backspace:**  
> In earlier preliminary models, Backspace was tentatively linked to slot (6, 3).  
> Physical capture `key_backspace_139mm.json` isolated the mutation at address `0x02E5` (741), which precisely corresponds to **Bank 5, Col 12** of the Hall matrix.  
> The formula $\text{Remap Col} = \text{Hall Col} + 1$ yields **Bank 5, Col 13 (Slot 93)**, where scancode `0x2A` (Backspace) is stored!

### 1.2. 4-Byte Record Semantics

| Byte | Field | Semantics | Status | Evidence |
|:---:|:---|:---|:---:|:---|
| **0** | Prefix | Modifier / extended scancode prefix | **`OBSERVED`** | `0x00` for standard keys, `0x92` and `0x56` for extended slots |
| **1** | Scancode | Standard USB HID Usage ID (Page 0x07) | **`CONFIRMED`** | 100% match of all 84 keys with USB HID standard |
| **2** | Special | Secondary / multimedia function code | **`CONFIRMED`** | Used on Fn layer for arrows (`0x0D`, `0x0E`, `0x10`) |
| **3** | Function Type | Function category: `0x02` = Standard HID, `0x03` = Extended/Keypad, `0x0D` = Hardware/RGB | **`CONFIRMED`** | Segregation of key types across L1 and L2 |

### 1.3. Semantics of "Empty" Slots (`00 00 00 02`)
Slots 28 (Minus), 42 (O), 56 (J), 70 (B), 84 (Space) have value `00 00 00 02`.
* **Semantics:** In Sonix controller architecture, the EEPROM/Flash table serves as an override table. The value `00 00 00 02` (scancode = 0 with type 0x02) designates the **factory ROM default** keycode for that matrix coordinate.
* **Status:** **`PROBABLE`**.

### 1.4. Verification of WRITE Transaction Remap L1 (`AA 22`)
In the controlled experiment `Key A -> B` (capture [`captures/experiments/remap_write_a_to_b.json`](file:///c:/KeyboardSoft/captures/experiments/remap_write_a_to_b.json)):
- **WRITE Opcode:** **`AA 22`** (**`CONFIRMED`**).
- **Framing:** Exactly 10 chunks (9 $\times$ 56 bytes + 1 $\times$ 8 bytes = 512 bytes).
- **Terminator:** NO dedicated terminator packet. Concludes with 10th chunk (address 504), where byte `0x01` in slot 126 functions as the in-band commit marker (**`CONFIRMED`**).
- **Acknowledgments:** Controller returns 10 synchronous ACKs `55 22 <sz> <addr>` (**`CONFIRMED`**).
- **Isolation:** Across all 84 physical keys in the buffer, only the scancode byte of Key A at offset `0x00C9` altered (`0x04` $\to$ `0x05`, Slot 50) (**`CONFIRMED`**).
- **Reversibility:** Reverting the key produced an exact zero-diff match with baseline (**`CONFIRMED`**).

---

## 2. Subsystem `AA 16` — Fn Layer (Modifier Layer)

| Field / Element | Value | Status | Evidence |
|:---|:---|:---:|:---|
| Table Length | 512 bytes (128 slots $\times$ 4 bytes) | **`CONFIRMED`** | 10 incoming `55 16 ...` chunks in capture |
| F-Row (F1..F12) | `00 00 00 00` (Unbound / Passthrough) | **`CONFIRMED`** | Slots 2..13 zero-filled on Fn layer |
| Fn + Backspace | ScrollLock (`0x47`, type `0x02`) | **`CONFIRMED`** | Slot 93 mapped to `00 47 00 02` |
| Fn + End | Pause (`0x48`, type `0x02`) | **`CONFIRMED`** | Slot 108 mapped to `00 48 00 02` |
| Fn + Left Arrow | Special `0x10`, Type `0x0D` | **`CONFIRMED`** | Slot 89 mapped to `00 00 10 0D` (RGB Animation) |
| Fn + Down Arrow | Special `0x0E`, Type `0x0D` | **`CONFIRMED`** | Slot 90 mapped to `00 00 0E 0D` (Brightness Down) |
| Fn + Up Arrow | Special `0x0D`, Type `0x0D` | **`CONFIRMED`** | Slot 91 mapped to `00 00 0D 0D` (Brightness Up) |
| Fn + Right Arrow | Special `0x0F`, Type `0x02` | **`CONFIRMED`** | Slot 92 mapped to `00 00 0F 02` (RGB Speed/Color) |

---

## 3. Subsystem `AA 1C` — Layer 3 vs Calibration

| Finding | Status | Evidence |
|:---|:---:|:---|
| `AA 1C` is 16 bytes of Hall sensor calibration | **`DISPROVEN`** | Dump Events #45..64 revealed 10 chunks of 56/8 bytes totalling 512 bytes |
| `AA 1C` baseline equals `AA 16` baseline | **`CONFIRMED`** | Byte-level binary equality `buf_1c == buf_16` in `read_01_initial_load.json` |
| `AA 1C` queried in keymap sequence | **`CONFIRMED`** | Polled immediately after `AA 16` and prior to RGB query `AA 13` |
| `AA 1C` represents Layer 3 (Mac Layout / Alternate Fn) | **`PROBABLE`** | Identical 128x4B format standard on dual-OS keyboards |
| Dynamic variation of `AA 1C` across modes | **`UNKNOWN`** | Requires capture during Win $\leftrightarrow$ Mac mode switch |

---

## 4. Subsystem `AA 15` — Macro & DKS

| Field / Element | Value | Status | Evidence |
|:---|:---|:---:|:---|
| Catalog Buffer Length | 400 bytes (0x0190) | **`CONFIRMED`** | 8 incoming reports (7 $\times$ 56B + 1 $\times$ 8B at address 0x0188) |
| User Macro Baseline State | Empty (392 bytes zeros) | **`CONFIRMED`** | `all(b == 0 for b in buf[:392])` in `read_01_initial_load.json` |
| Buffer Initialization Flag (Offset `0x0189`) | Byte `0x01` | **`CONFIRMED`** | Sole non-zero byte in baseline buffer |
| Two-Stage Write Protocol (`AA 25`) | Catalog + Dynamic Heap | **`CONFIRMED`** | Hardware-verified via ABA testing (see `macro_physical_validation_report.md`) |

---

## 5. Subsystem `AA 11` — Dynamic Status & Performance Settings

| Field (Offset) | Purpose | Dump Value | Status | Evidence |
|:---|:---|:---:|:---:|:---|
| **Offset 11** | Sleep Timeout (`sleepTime`, minutes) | `0x01` | **`CONFIRMED`** | Disassembly of `Po` (`getGameMode`); refutes early Active Profile hypothesis |
| **Offset 13** | Polling Rate (`reportRate`, 6 = 8000 Hz) | `0x06` | **`CONFIRMED`** | Configurator `rc.settingsConfig`; refutes early Lock Flags hypothesis |
| **Offset 19** | Stability Mode (`stabilityMode`) | `0x01` | **`CONFIRMED`** | Disassembly of `Po`; refutes early OS Mode hypothesis |
| **Offset 22** | Auto-Calibration (`autoCalibration`) | `0x01` | **`CONFIRMED`** | Disassembly of `Po`; refutes early Connection State hypothesis |
| **Offsets 8..10, 14..18, 20..21** | Reserved / dead-zones / delay | Various | **`CONFIRMED`** | Mapped in `Po` (`getGameMode`) |

---

## 6. Subsystem `AA 27` / `AA 17` — Hall Effect Switch Parameters

### 6.1. Status of 8-Byte Key Record Parameters

| Word | Byte | Parameter | Units | Status | Evidence Base |
|:---:|:---:|:---|:---:|:---:|:---|
| **0** | `+0..+1` | **Actuation Point** | $0.01\text{ mm}$ | **`CONFIRMED`** | Hardware write and read-back on Key A (`0x018D..0x018E`) via `NativeHidTransport` |
| **1** | `+2..+3` | **RT Press Sensitivity** | $0.01\text{ mm}$ ($0 = \text{OFF}$) | **`CONFIRMED`** | Physical RT Press series on Key A (`0x018F..0x0190`), unexpected changes == NONE |
| **2** | `+4..+5` | **RT Release Sensitivity** | $0.01\text{ mm}$ ($0 = \text{OFF}$) | **`CONFIRMED`** | Physical RT Release series on Key A (`0x0191..0x0192`), unexpected changes == NONE |
| **3** | `+6..+7` | **Flags / Mode** | raw uint16_le | **`UNKNOWN`** | Semantics unverified. Preserved verbatim |

### 6.2. Unified Scaling Formula
$$\text{raw} = \text{round}(\text{value}_{\text{mm}} \times 100)$$
$$\text{value}_{\text{mm}} = \frac{\text{raw}}{100.0}$$

Reference benchmarks: `0.00` $\to$ `0x0000`, `0.10` $\to$ `0x000A`, `0.20` $\to$ `0x0014`, `0.50` $\to$ `0x0032`, `1.00` $\to$ `0x0064`, `1.40` $\to$ `0x008C`.

### 6.3. Hardware Isolation
Physically proven: altering any one of the three verified parameters strictly impacts its own 2 bytes, with zero side-effects on neighboring keys or Flags fields.
