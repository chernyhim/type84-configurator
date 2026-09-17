# Changelog

All notable changes to the **IO by Red Square Type 84 Magnetic Black** reverse-engineering toolkit and configurator will be documented in this file.

---

## [1.0.0-rc1] - 2026-09-16

First release candidate featuring complete hardware protocol support, a safe multi-tab GUI configurator, offline mock simulation, JSON profile management, and comprehensive diagnostic CLI tools.

### Protocol Modules Status: 9/9 CLOSED

All 9 hardware protocol modules for the IO Type 84 keyboard (VID `0x0C45`, PID `0x80D6`) are completely CLOSED and physically verified against physical hardware:

1. **Remap Layer 1 (Base Layer)**: `AA 12` (read) / `AA 22` (write) — **[CLOSED]**
   * 1008-byte configuration, 10 chunks per transaction.
   * Full key scancode remapping for all 84 physical switch positions.
   * Dedicated DKS prefix linking (`0x08`).
2. **Remap Layer 2 (Fn Layer)**: `AA 16` (read) / `AA 26` (write) — **[CLOSED]**
   * Dual-layer custom binding with physical protection of hardware system functions (`F1..F12`, `Fn` key).
3. **Default Fn Layer**: `AA 1C` (read) — **[CLOSED]**
   * Read-only factory baseline used for hardware fallback and reference mapping.
4. **RGB Global Backlight**: `AA 13` (read) / `AA 23` (write) — **[CLOSED]**
   * Complete verified catalog of 26 hardware lighting effects.
   * Brightness (0–5), speed (1–5), color mode (Single / Rainbow), direction, secondary colors, and custom modes.
5. **RGB Per-Key Matrix**: `AA 14` (read) / `AA 24` (write) — **[CLOSED]**
   * Individual 24-bit RGB control for each of the 84 physical LED positions.
   * Direct color palette picking, marquee/drag selection, multi-key paint.
6. **Hall Effect & Rapid Trigger**: `AA 17` (read) / `AA 27` (write) — **[CLOSED]**
   * Dual profile slots (`profile=1` default, `profile=2`).
   * Canonical `<BBHHH` wire layout (`axis_type`, `flags`, `actuation`, `rt_press`, `rt_release`).
   * Dense physical addressing `(bank * 16 + col) * 8` (no bank preamble offset).
   * Rapid Trigger linked strictly to `flags & 0x01` (`isWholeFast`).
   * Closed-loop physical ABA verification PASS (zero collateral changes, bit-for-bit restore).
7. **Dynamic Keystrokes (DKS)**: `AA 18` (read) / `AA 28` (write) — **[CLOSED]**
   * 16-byte record layout across 64 hardware slots (1024-byte payload, 19 chunks).
   * 4 actuation depth thresholds (Make 1/2, Break 1/2) with sub-millimeter precision.
   * 4 actions with independent Down/Up state matrices, TAP/HOLD nibble semantics, and Layer 1 coordination.
8. **Settings & Game Mode**: `AA 11` (read) / `AA 21` (write) — **[CLOSED]**
   * 56-byte payload single-packet transaction (`AA 21`).
   * Report rate selection (125 Hz, 250 Hz, 500 Hz, 1000 Hz, 2000 Hz, 4000 Hz, 8000 Hz).
   * Windows key lock (Game Mode), stability modes, sleep timers, deadzone settings.
9. **Macros**: `AA 15` (read) / `AA 25` (write) — **[CLOSED]**
   * 400-byte catalog with 100 macro pointer slots + variable action heap.
   * Header framing, microsecond/millisecond delays, key press/release event sequences.
   * Two-stage write transaction with `55 25` ACK validation.

---

### Key Features

* **Safe GUI Configurator (`type84-gui` / `kb-re gui`)**:
  * Built with modern dark-themed CustomTkinter.
  * Interactive 84-key virtual keyboard reflecting live status, selections, and modified states.
  * Dedicated tabs: *RGB Global*, *Per-Key RGB*, *Key Remap (L1/L2)*, *Hall / Rapid Trigger*, *DKS*, *Macros*, and *Settings*.
  * Built-in Mock Mode (`--mock`) allowing 100% offline configuration, testing, and exploration without connected hardware.
* **Safety-First Write Pipeline**:
  * UI strictly mutates an isolated `working_profile`. The `device_state` baseline remains untouched until explicit confirmation.
  * Interactive Dry-Run Confirmation Dialog before any physical HID write: displays exact opcodes, packet count, modified keys, and before/after values.
  * Verification readback after writes with rollback on unexpected offset mutations.
  * Global **Discard Changes** button to immediately revert unsaved working profile edits.
* **Profile Management**:
  * Export working configurations to standard human-readable JSON files (`Save Profile...`).
  * Import previously saved profiles (`Load Profile...`) with automatic diff calculation against device hardware state.
* **Command-Line Interface (`kb-re`)**:
  * Packet capture parsers, assemblers, differ, batch forensic analyzers.
  * Wireshark and hex dump import tools.
  * Direct passive inspection commands: `kb-re inspect`, `kb-re plan`, `kb-re rgb-catalog`.
  * Safe actuation calibration utility: `kb-re write`.
  * GUI launcher: `kb-re gui [--mock]`.
