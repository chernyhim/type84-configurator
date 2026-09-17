#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Physical experiment: Testing how firmware handles primary_color and color_mode in AA23.

Variants:
  A: primary=(255, 0, 0) [FF 00 00], color_mode=0 [00]
  B: primary=(0, 255, 0) [00 FF 00], color_mode=0 [00]
  C: primary=(0, 0, 255) [00 00 FF], color_mode=0 [00]
  D: primary=(255, 255, 255) [FF FF FF], color_mode=1 [01]

Always restores baseline AA13 at the end.
"""

import copy
import sys
import time

from keyboard_re.transport.native_hid import NativeHidTransport
from keyboard_re.protocol.packets import REPORT_SIZE
from keyboard_re.protocol.rgb import build_rgb_global
from keyboard_re.protocol.read import parse_rgb_global_read_response, read_rgb_global


def read_raw_aa13(transport, timeout=2.0) -> bytes:
    req = bytearray(REPORT_SIZE)
    req[:8] = bytes.fromhex("AA 13 10 00 00 00 01 00")
    transport.send_report(0, bytes(req))
    return transport.receive_report(timeout=timeout)


def send_aa23_and_get_ack(transport, pkt: bytes, timeout=2.0) -> bytes:
    transport.send_report(0, pkt)
    return transport.receive_report(timeout=timeout)


def main():
    transport = NativeHidTransport()
    try:
        transport.open()
    except Exception as e:
        print(f"FAILED to open HID transport: {e}")
        sys.exit(1)

    try:
        # 1. Read initial baseline
        baseline_raw = read_raw_aa13(transport)
        baseline_cfg = parse_rgb_global_read_response(baseline_raw)
        print("=" * 70)
        print("BASELINE AA13:")
        print(f"  Raw [8:24]: {baseline_raw[8:24].hex(' ').upper()}")
        print(f"  Effect:     {baseline_cfg.effect} (0x{baseline_cfg.effect:02X})")
        print(f"  Primary:    {baseline_cfg.primary} (#{baseline_cfg.primary[0]:02X}{baseline_cfg.primary[1]:02X}{baseline_cfg.primary[2]:02X})")
        print(f"  Color Mode: {baseline_cfg.color_mode}")
        print(f"  Speed:      {baseline_cfg.speed}")
        print(f"  Brightness: {baseline_cfg.brightness}")
        print("=" * 70)

        variants = [
            ("A", (255, 0, 0), 0, "primary=FF0000, color_mode=00"),
            ("B", (0, 255, 0), 0, "primary=00FF00, color_mode=00"),
            ("C", (0, 0, 255), 0, "primary=0000FF, color_mode=00"),
            ("D", (255, 255, 255), 1, "primary=FFFFFF, color_mode=01"),
        ]

        results = []

        for name, primary, color_mode, desc in variants:
            print(f"\n--- Variant {name}: {desc} ---")
            # Build AA23 keeping all other baseline fields identical
            aa23_pkt = build_rgb_global(
                effect=baseline_cfg.effect,
                primary=primary,
                secondary=baseline_cfg.secondary,
                brightness=baseline_cfg.brightness,
                speed=baseline_cfg.speed,
                reserved_header=baseline_cfg.reserved_header,
                reserved_mid=b"\x00\x00",
                reserved_tail=b"\x00\x00\x00",
                color_mode=color_mode,
                direction=baseline_cfg.direction,
                effect_mode_type=baseline_cfg.effect_mode_type,
                driver_setting=255,
                reserved_byte21=baseline_cfg.reserved_byte21,
                magic=b"\xAA\x55",
            )
            print(f"  AA23 Send [8:24]: {aa23_pkt[8:24].hex(' ').upper()}")

            # 1. Send AA23
            ack = send_aa23_and_get_ack(transport, aa23_pkt)
            print(f"  ACK received:     {ack[:8].hex(' ').upper()} (Opcode 0x{ack[1]:02X})")

            # Small pause for firmware flash sync
            time.sleep(0.05)

            # 2. Read AA13
            read_resp = read_raw_aa13(transport)
            read_cfg = parse_rgb_global_read_response(read_resp)

            raw_8_23 = read_resp[8:24]
            print(f"  AA13 Read [8:24]: {raw_8_23.hex(' ').upper()}")
            print(f"  Decoded Primary:    {read_cfg.primary} (#{read_cfg.primary[0]:02X}{read_cfg.primary[1]:02X}{read_cfg.primary[2]:02X})")
            print(f"  Decoded Color Mode: {read_cfg.color_mode}")

            results.append({
                "variant": name,
                "desc": desc,
                "write_primary": primary,
                "write_color_mode": color_mode,
                "write_hex_8_23": aa23_pkt[8:24].hex(" ").upper(),
                "ack_hex": ack[:8].hex(" ").upper(),
                "read_hex_8_23": raw_8_23.hex(" ").upper(),
                "read_primary": read_cfg.primary,
                "read_color_mode": read_cfg.color_mode,
            })

            time.sleep(0.05)

        # 3. RESTORE BASELINE
        print("\n" + "=" * 70)
        print("RESTORING BASELINE AA13...")
        restore_aa23 = build_rgb_global(
            effect=baseline_cfg.effect,
            primary=baseline_cfg.primary,
            secondary=baseline_cfg.secondary,
            brightness=baseline_cfg.brightness,
            speed=baseline_cfg.speed,
            reserved_header=baseline_cfg.reserved_header,
            reserved_mid=b"\x00\x00",
            reserved_tail=b"\x00\x00\x00",
            color_mode=baseline_cfg.color_mode,
            direction=baseline_cfg.direction,
            effect_mode_type=baseline_cfg.effect_mode_type,
            driver_setting=255,
            reserved_byte21=baseline_cfg.reserved_byte21,
            magic=b"\xAA\x55",
        )
        restore_ack = send_aa23_and_get_ack(transport, restore_aa23)
        print(f"Restore ACK: {restore_ack[:8].hex(' ').upper()}")
        time.sleep(0.05)

        final_raw = read_raw_aa13(transport)
        final_cfg = parse_rgb_global_read_response(final_raw)
        print(f"Final Read [8:24]:  {final_raw[8:24].hex(' ').upper()}")
        print(f"Final Decoded:      primary={final_cfg.primary}, color_mode={final_cfg.color_mode}")
        if final_cfg.primary == baseline_cfg.primary and final_cfg.color_mode == baseline_cfg.color_mode and final_cfg.effect == baseline_cfg.effect:
            print("[OK] Baseline successfully restored!")
        else:
            print("[WARN] Baseline differs!")
        print("=" * 70)

        # Print summary table
        print("\nSUMMARY TABLE:")
        for r in results:
            w_prim_hex = f"#{r['write_primary'][0]:02X}{r['write_primary'][1]:02X}{r['write_primary'][2]:02X}"
            r_prim_hex = f"#{r['read_primary'][0]:02X}{r['read_primary'][1]:02X}{r['read_primary'][2]:02X}"
            print(f"Variant {r['variant']}:")
            print(f"  WRITE:    primary={w_prim_hex} ({r['write_primary']}), color_mode={r['write_color_mode']}")
            print(f"  READBACK: primary={r_prim_hex} ({r['read_primary']}), color_mode={r['read_color_mode']}")
            print(f"  Raw 8..23: {r['read_hex_8_23']}")

    finally:
        transport.close()


if __name__ == "__main__":
    main()
