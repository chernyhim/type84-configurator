# Type 84 RGB Lighting Control Protocol

> **Important Safety Note:**  
> This document and the related tooling are intended **strictly for passive analysis** of the official configurator traffic.  
> Direct transmission of arbitrary control packets to hardware was not performed without sandbox verification.

---

## 1. Hardware Interface & Transmission Channels

| Parameter | Value | Status | Notes |
| :--- | :---: | :---: | :--- |
| **Vendor ID (VID)** | `0x0C45` | `CONFIRMED` | Sonix Technology Co., Ltd. |
| **Product ID (PID)** | `0x80D6` | `CONFIRMED` | IO Type 84 Magnetic Black |
| **HID Usage Page** | `0xFF68` | `CONFIRMED` | Vendor-defined device configuration page |
| **HID Usage** | `0x0061` | `CONFIRMED` | Top-Level Application Collection (Type 1) |
| **Transmission Channel** | HID Output Report | `CONFIRMED` | Invokes `HIDDevice.prototype.sendReport(0, data)` |
| **Report ID** | `0` | `CONFIRMED` | Fixed zero Report ID |
| **Report Length** | `64` bytes | `CONFIRMED` | All reports are strictly 64 bytes in length |

---

## 2. Command Separation & Protocol Opcodes

The device protocol strictly segregates functional subsystems at the upper opcode level:

| Preamble Opcode | Purpose | Payload Block Size | Status |
| :---: | :--- | :---: | :---: |
| **`AA 23 10`** | **RGB Global Config:** Mode, primary color, brightness, speed | 24 bytes | `CONFIRMED` |
| **`AA 24 38`** | **RGB Per-Key Matrix Data:** Per-key LED colors (56-byte chunk) | 512 bytes (128 slots) | `CONFIRMED` |
| **`AA 24 08`** | **RGB Per-Key Matrix Tail:** Terminal chunk (8 bytes at address `0x01F8`) | 8 bytes | `CONFIRMED` |
| **`AA 27 38`** | **Hall Effect Key Matrix:** Actuation thresholds and Rapid Trigger | 1008 bytes (84 keys) | `CONFIRMED` |
| **`AA 27 10`** | **Hall Effect Key Matrix Terminator:** Commits analog thresholds | 7 bytes | `CONFIRMED` |

> [!IMPORTANT]
> **The RGB protocol is completely decoupled from key switch configuration (`AA 27 ...`):**  
> - Switching lighting effects, brightness, speed, and global colors is performed via a single `AA 23 10` report.
> - Per-key configuration is transmitted as a sequence of 1 packet of `AA 23 10` (activating Custom mode `0x80`) and 10 packets of `AA 24` (transferring the 512-byte LED buffer).
> - Analog switch configurations (`AA 27`) are completely unaffected during RGB adjustments.

---

## 3. RGB Global Packet Structure (`AA 23 10`)

The active payload size is 24 bytes (the remaining 40 bytes up to 64 bytes are zero-padded with `0x00`).

```text
Byte offset:
 0  1  2 |  3  4  5  6  7 |  8   |  9 10 11 | 12 13 14 | 15 16 | 17 | 18 | 19 20 21 | 22 23
AA 23 10 | 00 00 00 01 00 | Mode |  R  G  B  |  R  G  B  | 00 00 | Br | Sp | 00 00 00 | AA 55
```

### 3.1. Field Breakdown

| Byte | Field | Type | Range / Values | Purpose / Verification |
| :---: | :--- | :---: | :---: | :--- |
| **0..2** | **Opcode** | `3 bytes` | `AA 23 10` | Global lighting command preamble (`CONFIRMED`) |
| **3..7** | **Header** | `5 bytes` | `00 00 00 01 00` | Fixed configurator subheader (`CONFIRMED`) |
| **8** | **Effect Mode ID** | `uint8` | `0x01` .. `0x80` | **Lighting animation mode:**<br>• `0x01` = Static (`CONFIRMED`)<br>• `0x07` = Breathing (`CONFIRMED`)<br>• `0x80` = Custom / Per-Key (`CONFIRMED`) |
| **9..11** | **Primary Color** | `3 x uint8` | `0x00..0xFF` | Primary RGB color: `R` (byte 9), `G` (byte 10), `B` (byte 11). Red = `FF 00 00` (`CONFIRMED`) |
| **12..14** | **Secondary Color**| `3 x uint8` | `0x00..0xFF` | Secondary / active RGB888 color (`CONFIRMED`) |
| **15..16**| **Reserved** | `2 bytes` | `00 00` | Reserved |
| **17** | **Brightness** | `uint8` | `0..5` | **Brightness level (discrete scale 0..5):**<br>• `5` = 100% (maximum)<br>• `3` = 50% (medium)<br>• `0` = off (`CONFIRMED`) |
| **18** | **Speed** | `uint8` | `1..5` | **Dynamic animation speed (scale 1..5):**<br>• `1` = minimum<br>• `3` = default<br>• `5` = maximum (`CONFIRMED`) |
| **19..21**| **Reserved** | `3 bytes` | `00 00 00` | Reserved |
| **22..23**| **Magic Signature**| `uint16_be`| `AA 55` | Hardware validation/commit marker evaluated by controller (`CONFIRMED`) |
| **24..63**| **Zero Padding** | `40 bytes`| `00 ... 00` | Report alignment padding to 64 bytes |

---

## 4. Per-Key RGB Lighting Structure (`AA 24`)

When Custom mode is activated (`Mode = 0x80`), an LED layout buffer of exactly **512 bytes** is written into controller memory.

### 4.1. Memory Chunk Transmission Format (10 packets)
- Packets 1..9: Preamble `AA 24 38 <Addr_Lo> <Addr_Hi>` (56 bytes payload).
  - Addresses: `0x0000`, `0x0038`, `0x0070`, `0x00A8`, `0x00E0`, `0x0118`, `0x0150`, `0x0188`, `0x01C0` ($9 \times 56 = 504$ bytes).
- Packet 10: Preamble `AA 24 08 F8 01` (8 bytes payload at address `0x01F8` = 504).
- Total: $504 + 8 = \mathbf{512\text{ bytes}}$.

### 4.2. Per-Key LED Slot Format (4 bytes)
The 512-byte buffer is structured into **128 slots of 4 bytes each**:

```text
Slot i (offset i * 4):
+---------------+---------------+---------------+---------------+
|  Byte 0 (+0)  |  Byte 1 (+1)  |  Byte 2 (+2)  |  Byte 3 (+3)  |
|      Red      |     Green     |     Blue      |    LED ID     |
|     uint8     |     uint8     |     uint8     |  uint8 (= i)  |
+---------------+---------------+---------------+---------------+
```

### 4.3. Correlation with Physical Layout (Key W)
In an experiment where key `W` was set to green (`rgb_05_per_key.json`):
- Exclusively **Slot #35** altered (`LED ID = 0x23`, byte offset `140..143`):
  - Before (Red): `FF 00 00 23`
  - After (Green): `00 FF 00 23`
- **LED String Topology Analysis:**
  - **Row 0 (16 positions, indices 0..15):** `Esc`, `F1`–`F12`, `Print`, `Home`, `End`.
  - **Row 1 (17 positions, indices 16..32):** `~`, `1`–`0`, `-`, `=`, `Backspace` (2 slots for split/ISO), `Ins`, `PgUp`.
  - **Row 2 (indices starting at 33):**
    - `33`: `Tab`
    - `34`: `Q`
    - **`35`: `W`** $\to$ **100% empirical match!**

> [!NOTE]
> **Key Architectural Insight:**  
> LED routing strictly mirrors the **physical keyboard geometry** (top-to-bottom, left-to-right), **not the electrical Hall switch matrix**. In the switch matrix, key `W` resides at (Bank 2, Col 2), whereas in the LED matrix `W` has linear index 35.

---

## 5. Offline Encoder & Packet Construction

A pure software encoding layer is implemented in [`src/keyboard_re/protocol/rgb.py`](file:///c:/KeyboardSoft/src/keyboard_re/protocol/rgb.py).  
It generates exact 64-byte WebHID reports without requiring access to physical hardware.

### 5.1. Encoder API
- `build_rgb_global(effect, primary, secondary, brightness, speed)` $\to$ 64-byte packet `AA 23 10`.
- `build_led_buffer(colors)` $\to$ 512-byte buffer (128 slots $\times$ 4 bytes).
- `build_rgb_per_key_chunks(buffer)` $\to$ list of 10 reports of 64 bytes each (`AA 24 38` $\times$ 9 + `AA 24 08` $\times$ 1).
- `PHYSICAL_LED_MAP` $\to$ dedicated mapping between key identifiers and LED slot indices.

### 5.2. Field Status Classification

#### `CONFIRMED` (Hardware-verified via physical captures, 100% byte-for-byte reproducible):
1. **Report Preambles:** `AA 23 10` (Global RGB), `AA 24 38` (Per-Key Data Chunk), `AA 24 08` (Per-Key Tail).
2. **Mode IDs (byte 8):** `0x01` = Static, `0x07` = Breathing, `0x80` = Custom / Per-Key.
3. **Color Fields:** Primary RGB888 (bytes 9..11), Secondary RGB888 (bytes 12..14).
4. **Brightness Level (byte 17):** Scale $0..5$ ($0$ = off, $3$ = 50%, $5$ = 100%).
5. **Animation Speed (byte 18):** Scale $1..5$ ($1$ = min, $5$ = max).
6. **Hardware Signature (bytes 22..23):** Value `0xAA55` at the end of global reports.
7. **Tail Alignment:** Exactly 40 zero bytes (`0x00`) in report `AA 23 10`.
8. **Per-Key LED Slot Structure:** 4 bytes `[Red, Green, Blue, LED_ID]`, where `LED_ID == slot_index`.
9. **Per-Key Sequence Format:** Exactly 10 reports (9 with 56 bytes payload + 1 with 8 bytes payload at address `0x01F8`).
10. **Key W Position:** LED index for `W` strictly equals **Slot #35** (`0x23`).

#### `UNKNOWN` (Physically present in captures, but full semantics unproven):
1. **Global Packet Bytes 3..7 (`00 00 00 01 00`):** Constant configurator subheader. May encode profile number (`Profile 1`) or command subtype.
2. **Bytes 15..16 (`00 00`):** Reserved bytes between colors and brightness.
3. **Bytes 19..21 (`00 00 00`):** Reserved bytes between speed and `AA 55` signature.
4. **Slot #126 in Per-Key Dump (`00 01 00 7E`):** Lone non-zero control slot at the end of the 512-byte buffer.

#### `INFERENCE` (Logically derived from topology, pending targeted hardware capture):
1. **Indices of other LEDs in `PHYSICAL_LED_MAP`:** Derived by sequential interpolation of the physical row relative to verified key `W` (slot 35) and the 16-key function row.
2. **Reserved Slots (30, 62, 64):** Presumed allocated by the manufacturer to accommodate alternative PCB layouts (ISO split-Enter, split-Backspace, split-Shift).
