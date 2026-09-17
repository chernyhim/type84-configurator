# Type 84 Status & Device State Protocol (STATUS PROTOCOL)

> **Important Safety Note:**  
> This document and the module [`src/keyboard_re/protocol/read.py`](file:///c:/KeyboardSoft/src/keyboard_re/protocol/read.py) are intended **strictly for passive analysis**.

---

## 1. Status Packet Purpose (`AA 11` $\leftrightarrow$ `55 11`)

The `AA 11` command queries dynamic runtime keyboard state:
- Active configuration profile index;
- Lock indicator status (Caps Lock, Scroll Lock, Win Lock);
- Operating system mode (Windows vs Mac);
- Connection mode (USB wired vs wireless).

The exchange consists of exactly **one message pair**:
- Host Request: `AA 11 38 00 00 00 01 00 [00 ... 00]` (64 bytes)
- Device Response: `55 11 38 00 00 00 01 00 ...` (64 bytes)

---

## 2. Structure of the 16-Byte State Block (Offsets 8..23)

Real dump from `read_01_initial_load.json` (Event #4):
```text
Offset 00: 55 11 38 00 00 00 01 00 00 00 00 01 00 06 00 00 00 00 00 01 00 00 01 00 [00 ... 00]
```

### Byte-Level Map:

| Offset | Hex Byte | Type | Purpose | Dump Value |
|:---:|:---:|:---:|:---|:---|
| **`0..2`** | `55 11 38` | Header | Response prefix (command `0x11`, length `0x38`) | Fixed |
| **`3..4`** | `00 00` | uint16_le | Base address (0) | `0` |
| **`5..7`** | `00 01 00` | Subheader | Packet subheader | Fixed |
| **`8..10`**| `00 00 00` | Reserved | Reserved | `0x00` |
| **`11`** | **`0x01`** | uint8 | **Active Profile Index** | **`1` (Profile 1 active)** |
| **`12`** | `0x00` | Reserved | Reserved | `0x00` |
| **`13`** | **`0x06`** | uint8 (bitmask) | **Lock Flags** (Caps / Scroll / WinLock) | **`0x06`** (bits 1 & 2: WinLock / CapsLock) |
| **`14..18`** | `00 00 00 00 00` | Reserved | Reserved | `0x00` |
| **`19`** | **`0x01`** | uint8 | **OS Mode Flag** | **`1` (Windows Mode)** |
| **`20..21`** | `00 00` | Reserved | Reserved | `0x00` |
| **`22`** | **`0x01`** | uint8 | **Connection State** | **`1` (USB Wired Active)** |
| **`23..63`** | `00 ... 00` | Padding | Zero padding to 64 bytes | `0x00` |

---

## 3. Research Status Classification

| Parameter | Status | Evidence Base |
|:---|:---:|:---|
| Status Opcode `AA 11` / `55 11` | **`CONFIRMED`** | Verified in initial load and reconnect dumps |
| Active profile index at byte 11 | **`CONFIRMED`** | Read `0x01`, matches active Profile 1 |
| Lock flags position (byte 13) | **`PROBABLE`** | Non-zero value `0x06` in default state |
| Windows/Mac mode flag (byte 19) | **`PROBABLE`** | Bit flag `0x01` |
| Wired connection flag (byte 22) | **`PROBABLE`** | Bit flag `0x01` on USB connection |
