# Investigation of Calibration & `AA 1C` Protocol (CALIBRATION / LAYER 3 PROTOCOL) Type 84

> **Important Safety Note:**  
> Any modification of Hall sensor calibration parameters carries the risk of degrading keyboard sensitivity.  
> Within this project, all speculative write modifications of calibration constants or uncontrolled packet transmissions are **strictly prohibited**.

---

## 1. Hardware Refutation of the "16-Byte Calibration" Hypothesis

In early preliminary notes, it was hypothesized that the `AA 1C` command performed synchronization of a 16-byte magnetic sensor calibration limit table.

**Direct byte-level analysis of the physical capture `read_01_initial_load.json` (Events #45 – #64) completely disproved this hypothesis:**
1. The `AA 1C` command does not consist of a single report, but rather a sequence of **exactly 10 packets** (Events #45..64).
2. Chunk sizes and addressing:
   - 9 chunks of 56 bytes (`sz = 0x38`) at addresses `0x0000`, `0x0038`, `0x0070`, ..., `0x01C0`.
   - 1 chunk of 8 bytes (`sz = 0x08`) at address `0x01F8`.
3. The total buffer size is exactly **512 bytes**.
4. **Data Identity:** The content of the 512-byte buffer `55 1C` **matches the buffer `55 16` (Layer 2 / Fn Layer) byte-for-byte**:
   ```python
   buf_16 == buf_1c  # True
   ```

---

## 2. Purpose of the `AA 1C` $\leftrightarrow$ `55 1C` Buffer

Because the structure, addressing, and size of the `55 1C` buffer (512 bytes, 128 four-byte records) 100% mirrors the tables of `AA 12` (Layer 1) and `AA 16` (Layer 2 / Fn Layer), the command **`AA 1C` represents Layer 3 (Alternate / Mac Layout)**:

* **Layer 1 (`AA 12`):** Base Windows layout (Base Layer).
* **Layer 2 (`AA 16`):** Fn modifier layer (Fn Layer).
* **Layer 3 (`AA 1C`):** Alternate layout layer (Mac Mode or auxiliary Fn layer for alternative profile).

On standard factory firmware under Windows, Layer 3 is pre-initialized as an identical copy of the Fn layer (`buf_16`).

---

## 3. Where is Hall Sensor Calibration Located?

Based on comprehensive analysis of all protocol subsystems:
1. **Calibration of stroke travel limits (ADC Min / Max):**
   - Either embedded within the 1008-byte switch configuration image (`AA 17` / `AA 18`), where 8 bytes are allocated per key (actuation, rt_press, rt_release, flags);
   - Or measured automatically by the microcontroller upon power-up (dynamic baseline resting auto-calibration of magnetic sensors).
2. **Official Configurator Behavior:**
   - Does not issue separate short calibration requests during standard page loading. All data received from the device is exhaustively accounted for by the command catalog: `10`, `11`, `12`, `16`, `1C`, `13`, `14`, `15`, `17`, `18`.

---

## 4. Research Status Classification

| Element | Status | Evidence Base |
|:---|:---:|:---|
| Exchange format `AA 1C` / `55 1C` (512 bytes in 10 chunks) | **`CONFIRMED`** | 10 request-response pairs in `read_01_initial_load.json` |
| Byte-level match `buf_1c == buf_16` in baseline | **`CONFIRMED`** | Direct binary comparison of capture buffers |
| Designation of `AA 1C` as Layer 3 (Mac/Alt layer) | **`PROBABLE`** | Structural identity with `AA 12` and `AA 16` |
| Discrete 16 bytes of calibration in `AA 1C` | **`REFUTED`** | Disproven by direct traffic analysis (actual size 512B) |
| Internal sensor auto-calibration by controller | **`PROBABLE`** | Architecture of Sonix-based magnetic keyboards |
