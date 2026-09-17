# Configuration Protocol Specification: IO by Red Square Type 84 Magnetic Black

> **Important Safety Note:**  
> This document and the related tooling are intended **strictly for passive analysis** of the official configurator traffic.  
> Arbitrary transmission of raw HID packets or speculative writes of unverified structures to the hardware is prohibited.

---

## 1. Hardware Identification

| Parameter | Value | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Vendor ID (VID)** | `0x0C45` | `CONFIRMED` | Extracted from device descriptors (Sonix) |
| **Product ID (PID)** | `0x80D6` | `CONFIRMED` | Extracted from device descriptors |
| **Device** | IO by Red Square Type 84 Magnetic Black | `CONFIRMED` | 84-key magnetic Hall Effect keyboard |
| **Physical Key Count** | 84 | `CONFIRMED` | Exactly 84 active configuration slots identified across memory dump |

---

## 2. Transport Layer & Framing

| Parameter | Value | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Transmission Channel** | HID OUTPUT report | `CONFIRMED` | Intercepted via `HIDDevice.prototype.sendReport` |
| **Report ID** | `0` | `CONFIRMED` | Passed to WebHID as `sendReport(0, ...)` |
| **Report Size** | `64` bytes | `CONFIRMED` | All outbound configuration packets are fixed at 64 bytes |
| **Packets per Session** | 18 data + 1 terminator (19) | `CONFIRMED` | Full write cycle comprises exactly 19 reports |
| **Data Packet Preamble** | `AA 27 38 <Addr_Lo> <Addr_Hi>` | `CONFIRMED` | 16-bit offset address (little-endian), 56-byte payload step (`0x38`) |
| **Packet Payload** | 56 bytes | `CONFIRMED` | 18 packets $\times$ 56 bytes = 1008-byte configuration image |
| **Session Terminator** | `AA 27 10 F0 03 01 00` | `CONFIRMED` | Encodes image length `0x03F0` (1008) and commit flag `0x0001` |

---

## 3. Configuration Memory Architecture (1008 Bytes)

The total size of the configuration image is exactly **1008 bytes** (addresses `0x0000` through `0x03EF`).

### 3.1. Bank Organization (128 Bytes per Bank)
The image is organized into 8 banks of 128 bytes each ($8 \times 128 = 1024$; the final 16 bytes are unused: $1024 - 16 = 1008$ bytes):
- **Banks 0..5:** Contain the configuration of the primary 84 physical keys.
- **Bank 6:** Right-hand navigation cluster.
- **Bank 7:** Reserved region (zero-filled).

Each bank begins with a **5-byte service header** (`00 00 00 00 00`).  
Following the header, individual 8-byte key records are stored sequentially.

### 3.2. Key Addressing Formula
For a key located at bank $\text{Bank}$ and column $\text{Column}$:

$$\text{Key Address} = \text{Bank} \times 128 + 5 + \text{Column} \times 8$$

---

## 4. Internal Structure of 8-Byte Key Record

Each key is described by a structure composed of **four 16-bit Little-Endian integers (`uint16_le`)**:

```text
Base key address: Addr = Bank * 128 + 5 + Column * 8
+-------------------+-------------------+-------------------+-------------------+
|  Word 0 (+0..+1)  |  Word 1 (+2..+3)  |  Word 2 (+4..+5)  |  Word 3 (+6..+7)  |
|  Actuation Point  |   RT Press Sens   |  RT Release Sens  |   Flags / Mode    |
|     uint16_le     |     uint16_le     |     uint16_le     |     uint16_le     |
+-------------------+-------------------+-------------------+-------------------+
```

### 4.1. Field Breakdown (CONFIRMED vs UNKNOWN)

| Offset | Type | Parameter | Units / Range | Status | Evidence |
| :---: | :---: | :--- | :--- | :---: | :--- |
| **`+0..+1`** | `uint16_le` | **Actuation Point** | $0.01\text{ mm}$ ($140 = 1.40\text{ mm}$; range $10..400$) | **`CONFIRMED`** | Physical writes & read-backs on Key A (`0x018D..0x018E`) |
| **`+2..+3`** | `uint16_le` | **Rapid Trigger Press Sensitivity** | $0.01\text{ mm}$ ($0 = \text{OFF}$, $20 = 0.20\text{ mm}$) | **`CONFIRMED`** | Physical writes & read-backs on Key A (`0x018F..0x0190`) |
| **`+4..+5`** | `uint16_le` | **Rapid Trigger Release Sensitivity**| $0.01\text{ mm}$ ($0 = \text{OFF}$, $10 = 0.10\text{ mm}$) | **`CONFIRMED`** | Physical writes & read-backs on Key A (`0x0191..0x0192`) |
| **`+6..+7`** | `uint16_le` | **Flags / Mode** | Unparsed (preserved as raw uint16_le) | **`UNKNOWN`** | Semantics unverified; preserved verbatim |

### 4.2. Physical Unit Encoding Formula
All three verified parameters (**Actuation**, **RT Press**, **RT Release**) share an identical scale factor: $1\text{ LSB} = 0.01\text{ mm}$:

$$\text{raw} = \text{round}(\text{value}_{\text{mm}} \times 100)$$
$$\text{value}_{\text{mm}} = \frac{\text{raw}}{100.0}$$

Values are encoded in **little-endian** (`<H`).

#### Benchmark Reference Values:
| Physical Value | Decimal raw | Hex (uint16) | Wire Bytes (LE) |
| :---: | :---: | :---: | :---: |
| **`0.00 mm` (OFF)** | `0` | `0x0000` | `00 00` |
| **`0.10 mm`** | `10` | `0x000A` | `0A 00` |
| **`0.20 mm`** | `20` | `0x0014` | `14 00` |
| **`0.50 mm`** | `50` | `0x0032` | `32 00` |
| **`1.00 mm`** | `100` | `0x0064` | `64 00` |
| **`1.40 mm`** | `140` | `0x008C` | `8C 00` |

### 4.3. Hardware Field Isolation (Case Study: Key A)
Coordinates of Key `A`: **Bank 3, Column 1** $\to$ base address `Addr = 3 * 128 + 5 + 1 * 8 = 397` (`0x018D`).

```text
0x018D..0x018E: Actuation Point (Word 0, CONFIRMED)
0x018F..0x0190: RT Press Sensitivity (Word 1, CONFIRMED)
0x0191..0x0192: RT Release Sensitivity (Word 2, CONFIRMED)
0x0193..0x0194: Flags / Mode (Word 3, UNKNOWN)
```

In controlled physical experiments using `NativeHidTransport`:
- Modifying Actuation strictly alters bytes `0x018D..0x018E`.
- Modifying RT Press strictly alters bytes `0x018F..0x0190`.
- Modifying RT Release strictly alters bytes `0x0191..0x0192`.
- The `Flags` field (`0x0193..0x0194`) remains `0x0000` and is **not required** to toggle Rapid Trigger on/off.
- In all experiments, before/after read-back diff demonstrated: `unexpected changes == NONE`.

### 4.4. Rapid Trigger Toggle Semantics (On / Off)
- **Enabling RT:** Write non-zero values into Word 1 (Press) and/or Word 2 (Release).
- **Disabling RT:** Zero out Words 1 and 2 (`0x0000`).
- A dedicated switch bit/byte within the key record **is not utilized**. An "RT Off" state is bit-for-bit identical to baseline dump state (`baseline_140mm.json`).

---

## 5. Two-Tier Model: Physical Geometry vs Electrical Matrix

The Type 84 configuration architecture is strictly decoupled into two layers:
1. **`PHYSICAL_LAYOUT` (Physical Geometry):** User-visible arrangement of 84 keys on the chassis and keycaps per reference image [`docs/type84_layout.png`](file:///c:/KeyboardSoft/docs/type84_layout.png).
2. **`ELECTRICAL_MATRIX` (Electrical/Configuration Matrix):** Microcontroller memory addressing using `(Bank, Column)` coordinates.

> [!WARNING]
> **Critical Architectural Rule:**  
> **A physical keyboard row DOES NOT equal a matrix Bank!**  
> Physical key layout matching `(Bank, Column)` coordinates cannot be declared `CONFIRMED` based solely on images.  
> Transitioning an entry to `CONFIRMED` status requires an isolated hardware capture experiment.

### 5.1. Proofs of Divergence Between Physical Rows and Matrix Banks
1. **Key Enter:**
   - Physically located in **Row 3** (right of `' "`).
   - Electrically verified in **Bank 4, Column 12** (`0x0265` = 613 in `exp_05_key_enter_actuation`).
2. **Bottom Modifier Row:**
   - Physically, Row 5 contains **exactly 10 keys**: `L-Ctrl`, `L-Win`, `L-Alt`, `Space`, `R-Alt`, `Fn`, `R-Ctrl`, `Left`, `Down`, `Right`.
   - In Bank 5 of the memory dump, **12 slots** are active (`cols: [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]`). Two slots are electrically routed to keys located in other physical rows.

---

### 5.2. Verified Distribution of Auxiliary Slots in Bank 5 and Bank 3

Based on direct hardware experiments (`key_up_139mm.json`, `key_backslash_139mm.json`, `key_backspace_139mm.json`), the peripheral key routing hypothesis was **fully verified**:

1. **`Up` (Up Arrow) $\to$ `Bank 5, Column 10` (`0x02D5` = 725) — `CONFIRMED` (`key_up_139mm.json`):**  
   Located in Bank 5 strictly between `Down` (Col 9) and `Right` (Col 11). All 4 arrow keys are hardware-grouped into a continuous block in Bank 5 (`Cols 8, 9, 10, 11`).
2. **`\|` (Backslash) $\to$ `Bank 3, Column 12` (`0x01E5` = 485) — `CONFIRMED` (`key_backslash_139mm.json`):**  
   Key `\|` occupies Column 12 of Bank 3 (freed up because `Enter` routes to Bank 4).
3. **`Backspace` $\to$ `Bank 5, Column 12` (`0x02E5` = 741) — `CONFIRMED` (`key_backspace_139mm.json`):**  
   Key `Backspace` is routed to Column 12 of Bank 5 (previously hypothesized as `Menu`).

---

### 5.3. Status of Preliminary Aliases (`PAUSE`, `MENU`, `BACKSPACE`)

| Key Name | Model Status | Analysis against Physical Layout [`type84_layout.png`](file:///c:/KeyboardSoft/docs/type84_layout.png) |
| :--- | :---: | :--- |
| **`PAUSE`** | `UNCONFIRMED_ALIAS` | `Pause` key is **not physically present** on keyboard. Preserved in codebase as a fallback alias. |
| **`MENU`** | `UNCONFIRMED_ALIAS` | `Menu` key is **not physically present** (bottom row has only 10 keys). Slot `(5, 12)` is physically verified as **`Backspace`**. |
| **`BACKSPACE`** | `CONFIRMED` | Hardware-confirmed at **Bank 5, Column 12** (`0x02E5` = 741) via `key_backspace_139mm.json`. Previous hypothesis of Bank 6 Col 3 refuted. |

---

### 5.4. Full 84-Key Summary Table

> **Confidence Level Legend:**  
> - `CONFIRMED` — Verified via isolated physical hardware capture experiment.  
> - `PROBABLE` — High confidence via continuous bank topology (adjacent to confirmed key).  
> - `HYPOTHESIS` — Navigation cluster topology hypothesis (pending dedicated capture).

| Key | Physical Row | Physical Column | Bank | Matrix Column | Address (Hex) | Address (Dec) | Evidence | Confidence |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| **Esc** | 0 | 0 | 0 | 0 | `0x0005` | 5 | Row 0 start | `PROBABLE` |
| **F1** | 0 | 1 | 0 | 1 | `0x000D` | 13 | Row 0 topology | `PROBABLE` |
| **F2** | 0 | 2 | 0 | 2 | `0x0015` | 21 | Row 0 topology | `PROBABLE` |
| **F3** | 0 | 3 | 0 | 3 | `0x001D` | 29 | Row 0 topology | `PROBABLE` |
| **F4** | 0 | 4 | 0 | 4 | `0x0025` | 37 | Row 0 topology | `PROBABLE` |
| **F5** | 0 | 5 | 0 | 5 | `0x002D` | 45 | Row 0 topology | `PROBABLE` |
| **F6** | 0 | 6 | 0 | 6 | `0x0035` | 53 | Row 0 topology | `PROBABLE` |
| **F7** | 0 | 7 | 0 | 7 | `0x003D` | 61 | Row 0 topology | `PROBABLE` |
| **F8** | 0 | 8 | 0 | 8 | `0x0045` | 69 | Row 0 topology | `PROBABLE` |
| **F9** | 0 | 9 | 0 | 9 | `0x004D` | 77 | Row 0 topology | `PROBABLE` |
| **F10** | 0 | 10 | 0 | 10 | `0x0055` | 85 | Row 0 topology | `PROBABLE` |
| **F11** | 0 | 11 | 0 | 11 | `0x005D` | 93 | Row 0 topology | `PROBABLE` |
| **F12** | 0 | 12 | 0 | 12 | `0x0065` | 101 | Row 0 topology | `PROBABLE` |
| **`~ (Grave)** | 1 | 0 | 1 | 0 | `0x0085` | 133 | Row 1 start | `PROBABLE` |
| **1** | 1 | 1 | 1 | 1 | `0x008D` | 141 | Row 1 topology | `PROBABLE` |
| **2** | 1 | 2 | 1 | 2 | `0x0095` | 149 | Row 1 topology | `PROBABLE` |
| **3** | 1 | 3 | 1 | 3 | `0x009D` | 157 | Row 1 topology | `PROBABLE` |
| **4** | 1 | 4 | 1 | 4 | `0x00A5` | 165 | Row 1 topology | `PROBABLE` |
| **5** | 1 | 5 | 1 | 5 | `0x00AD` | 173 | Row 1 topology | `PROBABLE` |
| **6** | 1 | 6 | 1 | 6 | `0x00B5` | 181 | Row 1 topology | `PROBABLE` |
| **7** | 1 | 7 | 1 | 7 | `0x00BD` | 189 | Row 1 topology | `PROBABLE` |
| **8** | 1 | 8 | 1 | 8 | `0x00C5` | 197 | Row 1 topology | `PROBABLE` |
| **9** | 1 | 9 | 1 | 9 | `0x00CD` | 205 | Row 1 topology | `PROBABLE` |
| **0** | 1 | 10 | 1 | 10 | `0x00D5` | 213 | Row 1 topology | `PROBABLE` |
| **- (Minus)** | 1 | 11 | 1 | 11 | `0x00DD` | 221 | Row 1 topology | `PROBABLE` |
| **= (Equal)** | 1 | 12 | 1 | 12 | `0x00E5` | 229 | Row 1 topology | `PROBABLE` |
| **Tab** | 2 | 0 | 2 | 0 | `0x0105` | 261 | Left of Q | `PROBABLE` |
| **Q** | 2 | 1 | 2 | 1 | `0x010D` | 269 | `exp_03_key_q_actuation` | **`CONFIRMED`** |
| **W** | 2 | 2 | 2 | 2 | `0x0115` | 277 | Between Q and E | `PROBABLE` |
| **E** | 2 | 3 | 2 | 3 | `0x011D` | 285 | Row 2 topology | `PROBABLE` |
| **R** | 2 | 4 | 2 | 4 | `0x0125` | 293 | Row 2 topology | `PROBABLE` |
| **T** | 2 | 5 | 2 | 5 | `0x012D` | 301 | Row 2 topology | `PROBABLE` |
| **Y** | 2 | 6 | 2 | 6 | `0x0135` | 309 | Row 2 topology | `PROBABLE` |
| **U** | 2 | 7 | 2 | 7 | `0x013D` | 317 | Row 2 topology | `PROBABLE` |
| **I** | 2 | 8 | 2 | 8 | `0x0145` | 325 | Row 2 topology | `PROBABLE` |
| **O** | 2 | 9 | 2 | 9 | `0x014D` | 333 | Row 2 topology | `PROBABLE` |
| **P** | 2 | 10 | 2 | 10 | `0x0155` | 341 | Row 2 topology | `PROBABLE` |
| **[{** | 2 | 11 | 2 | 11 | `0x015D` | 349 | Row 2 topology | `PROBABLE` |
| **]}** | 2 | 12 | 2 | 12 | `0x0165` | 357 | Row 2 topology | `PROBABLE` |
| **Caps** | 3 | 0 | 3 | 0 | `0x0185` | 389 | Left of A | `PROBABLE` |
| **A** | 3 | 1 | 3 | 1 | `0x018D` | 397 | `exp_01_key_a` | **`CONFIRMED`** |
| **S** | 3 | 2 | 3 | 2 | `0x0195` | 405 | `exp_01`, `exp_10`, `exp_11`, `exp_12` | **`CONFIRMED`** |
| **D** | 3 | 3 | 3 | 3 | `0x019D` | 413 | `exp_01_key_d` | **`CONFIRMED`** |
| **F** | 3 | 4 | 3 | 4 | `0x01A5` | 421 | `exp_02_key_f_actuation` | **`CONFIRMED`** |
| **G** | 3 | 5 | 3 | 5 | `0x01AD` | 429 | Right of F | `PROBABLE` |
| **H** | 3 | 6 | 3 | 6 | `0x01B5` | 437 | Row 3 topology | `PROBABLE` |
| **J** | 3 | 7 | 3 | 7 | `0x01BD` | 445 | Row 3 topology | `PROBABLE` |
| **K** | 3 | 8 | 3 | 8 | `0x01C5` | 453 | Row 3 topology | `PROBABLE` |
| **L** | 3 | 9 | 3 | 9 | `0x01CD` | 461 | Row 3 topology | `PROBABLE` |
| **;:** | 3 | 10 | 3 | 10 | `0x01D5` | 469 | Row 3 topology | `PROBABLE` |
| **'"** | 3 | 11 | 3 | 11 | `0x01DD` | 477 | Row 3 topology | `PROBABLE` |
| **\| (Backslash)**| 2 | 13 | 3 | 12 | `0x01E5` | 485 | `key_backslash_139mm.json` | **`CONFIRMED`** |
| **L-Shift** | 4 | 0 | 4 | 0 | `0x0205` | 517 | Left of Z | `PROBABLE` |
| **Z** | 4 | 1 | 4 | 1 | `0x020D` | 525 | `exp_04_key_z_actuation` | **`CONFIRMED`** |
| **X** | 4 | 2 | 4 | 2 | `0x0215` | 533 | Between Z and C | `PROBABLE` |
| **C** | 4 | 3 | 4 | 3 | `0x021D` | 541 | Row 4 topology | `PROBABLE` |
| **V** | 4 | 4 | 4 | 4 | `0x0225` | 549 | Row 4 topology | `PROBABLE` |
| **B** | 4 | 5 | 4 | 5 | `0x022D` | 557 | Row 4 topology | `PROBABLE` |
| **N** | 4 | 6 | 4 | 6 | `0x0235` | 565 | Row 4 topology | `PROBABLE` |
| **M** | 4 | 7 | 4 | 7 | `0x023D` | 573 | Row 4 topology | `PROBABLE` |
| **,<** | 4 | 8 | 4 | 8 | `0x0245` | 581 | Row 4 topology | `PROBABLE` |
| **.>** | 4 | 9 | 4 | 9 | `0x024D` | 589 | Row 4 topology | `PROBABLE` |
| **/?** | 4 | 10 | 4 | 10 | `0x0255` | 597 | Row 4 topology | `PROBABLE` |
| **R-Shift** | 4 | 11 | 4 | 11 | `0x025D` | 605 | Left of Up | `PROBABLE` |
| **Enter** | 3 | 12 | 4 | 12 | `0x0265` | 613 | `exp_05_key_enter_actuation` | **`CONFIRMED`** |
| **L-Ctrl** | 5 | 0 | 5 | 0 | `0x0285` | 645 | Row 5 start | `PROBABLE` |
| **L-Win** | 5 | 1 | 5 | 1 | `0x028D` | 653 | Between Ctrl and Alt | `PROBABLE` |
| **L-Alt** | 5 | 2 | 5 | 2 | `0x0295` | 661 | Left of Space | `PROBABLE` |
| **Space Bar** | 5 | 3 | 5 | 3 | `0x029D` | 669 | `exp_06_key_space_actuation` | **`CONFIRMED`** |
| **R-Alt** | 5 | 4 | 5 | 4 | `0x02A5` | 677 | Right of Space | `PROBABLE` |
| **Fn** | 5 | 5 | 5 | 5 | `0x02AD` | 685 | Between R-Alt and R-Ctrl | `PROBABLE` |
| **R-Ctrl** | 5 | 6 | 5 | 7 | `0x02BD` | 701 | Left of Left (Col 6 skipped) | `PROBABLE` |
| **Left ($\leftarrow$)**| 5 | 7 | 5 | 8 | `0x02C5` | 709 | `exp_07_key_left_actuation` | **`CONFIRMED`** |
| **Down ($\downarrow$)**| 5 | 8 | 5 | 9 | `0x02CD` | 717 | `exp_08_key_down_actuation` | **`CONFIRMED`** |
| **Up ($\uparrow$)** | 4 | 12 | 5 | 10 | `0x02D5` | 725 | `key_up_139mm.json` | **`CONFIRMED`** |
| **Right ($\rightarrow$)**| 5 | 9 | 5 | 11 | `0x02DD` | 733 | `exp_09_key_right_actuation` | **`CONFIRMED`** |
| **Backspace** | 1 | 13 | 5 | 12 | `0x02E5` | 741 | `key_backspace_139mm.json` | **`CONFIRMED`** |
| **End** (hyp. Col 3)| 0 | 15 | 6 | 3 | `0x031D` | 797 | Nav cluster (physically right of Home) | `HYPOTHESIS` |
| **Print** | 0 | 13 | 6 | 7 | `0x033D` | 829 | Nav cluster | `HYPOTHESIS` |
| **Ins** | 1 | 14 | 6 | 8 | `0x0345` | 837 | Nav cluster | `HYPOTHESIS` |
| **Del** | 2 | 14 | 6 | 9 | `0x034D` | 845 | Nav cluster | `HYPOTHESIS` |
| **Home** | 0 | 14 | 6 | 10 | `0x0355` | 853 | Nav cluster | `HYPOTHESIS` |
| **PgUp** | 1 | 15 | 6 | 11 | `0x035D` | 861 | Nav cluster | `HYPOTHESIS` |
| **PgDn** | 2 | 15 | 6 | 12 | `0x0365` | 869 | Nav cluster | `HYPOTHESIS` |

---

## 6. RGB Lighting Protocol (Subsystems `AA 23` and `AA 24`)

Full lighting protocol documentation is detailed in [`docs/RGB_PROTOCOL.md`](file:///c:/KeyboardSoft/docs/RGB_PROTOCOL.md).

### Key Architectural Takeaways:
1. **Strict Command Isolation:**
   - Hall switch analog matrix: **`AA 27`** (1008 bytes).
   - Global lighting (mode, color, brightness, speed): **`AA 23 10`** (24 bytes payload).
   - Per-Key LED matrix: **`AA 24`** (512 bytes = 128 slots $\times$ 4 bytes).
2. **Linear-Physical LED Addressing:**
   - Unlike Hall switches, LEDs are addressed strictly by **physical rows** (top-to-bottom, left-to-right).
   - Key `W` is hardware-confirmed at **Slot #35** (`LED ID = 0x23`, offset 140..143 in `rgb_05_per_key.json`).

---

## 7. Key Remap Protocol (Subsystems `AA 12` and `AA 22`)

Full layer matrix and key remapping documentation is detailed in [`docs/KEYMAP_PROTOCOL.md`](file:///c:/KeyboardSoft/docs/KEYMAP_PROTOCOL.md).

### Key Parameters of WRITE Transaction (`AA 22`):
- **Opcode:** `AA 22` (writes Layer 1 Base mapping, 512 bytes).
- **Framing:** 10 chunks (9 chunks $\times$ 56 bytes + 1 tail chunk $\times$ 8 bytes).
- **Terminator:** No dedicated commit packet (committed in-band in chunk 9 at address 504).
- **Acknowledgments (ACK):** 10 synchronous reports `55 22 <sz> <addr_lo> <addr_hi>`.
- **Semantics:** Confirmed isolated modification of Key `A` scancode at slot 50 (`0x00C8`, `0x04` $\to$ `0x05`).

---

## 8. Pure Offline Protocol Encoding Layer (`keyboard_re.protocol`)

For safe modeling and packet reproduction without physical controller access, the package [`src/keyboard_re/protocol/`](file:///c:/KeyboardSoft/src/keyboard_re/protocol/) provides:
- **`packets.py`**: USB HID metadata (Report ID 0, 64-byte length, Usage Page `0xFF68`, Usage `0x0061`) and alignment utilities.
- **`hall.py`**: Encoding and decoding for protocol `AA 27` (`build_hall_chunk`, `build_hall_write`, `build_hall_terminator`, `parse_hall_write`). Reproduces all 18 blocks and terminator byte-for-byte against physical dumps (`baseline_140mm.json`).
- **`rgb.py`**: Encoding and decoding of global packet `AA 23` and per-key buffer `AA 24` (`build_rgb_global`, `build_led_buffer`, `build_rgb_per_key_chunks`), along with decoupled physical LED map `PHYSICAL_LED_MAP`.
- **`keymap.py`**: Parsing and decoding of layer matrices (`KeymapTable`, `KeyRemapRecord`).

> [!CAUTION]
> The encoding layer is completely isolated from system I/O and contains no calls to `sendReport`, `sendFeatureReport`, or any other physical transmission mechanisms.
