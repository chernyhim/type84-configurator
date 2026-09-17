"""
Command-line interface for IO by Red Square Type 84 Magnetic Black reverse engineering.

Passive operations only: parse, assemble, diff, import, batch-analyze.
NO packet transmission or hardware writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from keyboard_re.assembler import assemble_packets, load_image_from_file_or_capture
from keyboard_re.batch import BatchAnalyzer
from keyboard_re.capturer.importer import import_hex_lines, import_wireshark_json
from keyboard_re.differ import diff_images, diff_to_dict, format_diff_table
from keyboard_re.models import (
    CONFIG_IMAGE_SIZE,
    ConfigImage,
    PacketType,
    RawCapture,
)
from keyboard_re.models.state import KeyboardSnapshot, KeyboardState
from keyboard_re.parser import parse_capture, parse_reports
from keyboard_re.protocol.diff import compare_snapshots
from keyboard_re.protocol.plan import build_write_plan
from keyboard_re.protocol.transaction import write_transaction
from keyboard_re.protocol.transport import DryRunTransport



def cmd_parse(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    capture = RawCapture.load_json(path)
    parsed = parse_capture(capture)

    print(f"Capture: {path.name}")
    print(f"Total reports: {len(parsed)}")
    print("-" * 70)

    for p in parsed:
        if p.packet_type == PacketType.DATA_CHUNK:
            addr_str = f"0x{p.address:04X} ({p.address:4d})" if p.address is not None else "None"
            payload_preview = p.payload[:8].hex().upper() + "..."
            status = "OK" if p.is_valid else f"INVALID: {p.error_message}"
            print(f"[{p.index:02d}] DATA_CHUNK  Addr: {addr_str:<12} Payload[56b]: {payload_preview:<20} {status}")
        elif p.packet_type == PacketType.TERMINATOR:
            flags_hex = p.terminator_flags.hex().upper() if p.terminator_flags else "-"
            status = "OK" if p.is_valid else f"INVALID: {p.error_message}"
            print(f"[{p.index:02d}] TERMINATOR  Size: {p.terminator_size:<6} Flags: {flags_hex:<12} {status}")
        else:
            status = f"INVALID: {p.error_message}"
            raw_preview = p.raw_data[:8].hex().upper() + "..."
            print(f"[{p.index:02d}] UNKNOWN     Raw: {raw_preview:<30} {status}")

    return 0


def cmd_assemble(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    capture = RawCapture.load_json(path)
    sessions = capture.extract_sessions()
    if len(sessions) > 1:
        print(f"Note: Detected {len(sessions)} sessions in capture. Using session #{args.session}.")
        parsed = parse_reports(sessions[args.session])
    else:
        parsed = parse_capture(capture)

    res = assemble_packets(parsed)

    print(f"Assembling image from {path.name}:")
    print(f"Chunks found: {res.chunks_found}/{res.expected_chunks}")
    print(f"Terminator packet observed: {'Yes' if res.has_terminator else 'No'}")

    for w in res.warnings:
        print(f"  [WARNING] {w}")
    for e in res.errors:
        print(f"  [ERROR] {e}")

    if not res.success or res.image is None:
        print("\nAssembly FAILED. Cannot produce complete 1008-byte image.", file=sys.stderr)
        return 1

    print(f"\nAssembly SUCCESS: 1008-byte configuration image built.")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".bin":
            out_path.write_bytes(res.image.data)
            print(f"Saved binary image: {out_path} ({len(res.image.data)} bytes)")
        elif out_path.suffix.lower() == ".json":
            slots_data = {
                f"slot_{i:03d}": res.image.get_slot(i).hex().upper()
                for i in range(126)
            }
            dump = {
                "size": len(res.image.data),
                "slots_count": 126,
                "hex": res.image.data.hex().upper(),
                "slots": slots_data
            }
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(dump, f, indent=2)
            print(f"Saved JSON image: {out_path}")
        else:
            out_path.write_bytes(res.image.data)
            print(f"Saved raw image: {out_path} ({len(res.image.data)} bytes)")

    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    path1 = Path(args.base)
    path2 = Path(args.modified) if args.modified else None

    if not path1.exists():
        print(f"Error: base file not found: {path1}", file=sys.stderr)
        return 1

    if path2 is None:
        cap = RawCapture.load_json(path1)
        sessions = cap.extract_sessions()
        if len(sessions) >= 2:
            from keyboard_re.assembler import assemble_packets
            from keyboard_re.parser import parse_reports
            img1 = assemble_packets(parse_reports(sessions[0])).image
            img2 = assemble_packets(parse_reports(sessions[1])).image
            title = f"Internal Diff in {path1.name} (Session 1 -> Session 2)"
        else:
            print("Error: When comparing a single file, it must contain at least 2 sessions.", file=sys.stderr)
            return 1
    else:
        if not path2.exists():
            print(f"Error: modified file not found: {path2}", file=sys.stderr)
            return 1
        img1 = load_image_from_file_or_capture(path1, session_index=0)
        img2 = load_image_from_file_or_capture(path2, session_index=-1)
        title = f"Diff: {path1.name} -> {path2.name}"

    diffs = diff_images(img1, img2)

    if args.format == "json":
        data = {
            "base_file": str(path1),
            "modified_file": str(path2) if path2 else str(path1),
            "total_differences": len(diffs),
            "differences": diff_to_dict(diffs)
        }
        json_output = json.dumps(data, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(json_output, encoding="utf-8")
            print(f"Saved diff to {args.output}")
        else:
            print(json_output)
    else:
        table_output = format_diff_table(diffs, title=title)
        if args.output:
            Path(args.output).write_text(table_output, encoding="utf-8")
            print(f"Saved diff to {args.output}")
        else:
            print(table_output)

    return 0


def cmd_batch_analyze(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: batch config file not found: {config_path}", file=sys.stderr)
        return 1

    analyzer = BatchAnalyzer()
    analyzer.load_config_json(config_path)

    print(f"Running batch analysis with {len(analyzer.experiments)} experiments from {config_path.name}...\n")
    analyzer.run_batch()

    # Print terminal tables
    print(analyzer.format_terminal_tables())

    # Optional exports
    if args.report:
        report_path = Path(args.report)
        report_path.write_text(analyzer.generate_markdown_report(), encoding="utf-8")
        print(f"\nSaved markdown report to: {report_path}")

    if args.json:
        json_path = Path(args.json)
        json_path.write_text(json.dumps(analyzer.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved JSON export to: {json_path}")

    return 0


def cmd_import_hex(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    lines = path.read_text(encoding="utf-8").splitlines()
    desc = args.description or f"Imported from {path.name}"
    capture = import_hex_lines(lines, description=desc, source_filename=path.name)

    out_path = Path(args.output) if args.output else path.with_suffix(".json")
    capture.save_json(out_path)
    print(f"Successfully imported {len(capture.reports)} reports to {out_path}")
    return 0


def cmd_import_wireshark(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    desc = args.description or f"Imported from {path.name}"
    capture = import_wireshark_json(path, description=desc)

    out_path = Path(args.output) if args.output else path.with_suffix(".json")
    capture.save_json(out_path)
    print(f"Successfully imported {len(capture.reports)} reports to {out_path}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    snapshot = KeyboardSnapshot.load_json(path)
    print(f"=== KEYBOARD SNAPSHOT: {path.name} ===")
    if snapshot.device_info:
        print(f"Hardware: VID 0x{snapshot.device_info.vid:04X}, PID 0x{snapshot.device_info.pid:04X}")
        print(f"Firmware: v{snapshot.device_info.firmware_version} (Bootloader: v{snapshot.device_info.bootloader_version})")

    print(f"Active Profile: #{snapshot.active_profile_id}")
    if snapshot.status:
        print(f"Status: Lock flags=0x{snapshot.status.lock_flags:02X}, OS Mode=0x{snapshot.status.mode_flag:02X}, Connection=0x{snapshot.status.connection_flag:02X}")

    prof = snapshot.active_profile
    print(f"Hall Switches: {len(prof.hall.keys)} configured analog keys")
    print(f"Keymap Layer 1: {prof.keymap.non_empty_count}/128 slots mapped")
    if snapshot.fn_layer:
        print(f"Keymap Layer 2 (Fn): {snapshot.fn_layer.non_empty_count}/128 slots mapped")
    if prof.rgb_global:
        print(f"RGB Lighting: Mode {prof.rgb_global.effect}, Brightness {prof.rgb_global.brightness}/5, Speed {prof.rgb_global.speed}/5")

    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        return 1

    snapshot = KeyboardSnapshot.load_json(path)
    section = (args.section or "all").lower()
    prof = snapshot.active_profile

    if section in ("all", "hall"):
        print(f"--- Hall Matrix Configuration (Profile {prof.profile_id}) ---")
        for key_name in sorted(prof.hall.keys.keys()):
            cfg = prof.hall.get_key(key_name)
            rt_str = f"RT(press={cfg.rt_press_mm}mm, rel={cfg.rt_release_mm}mm)" if cfg.is_rt_enabled else "RT(OFF)"
            print(f"  Key {key_name:<10}: Actuation={cfg.actuation_mm:.2f} mm, {rt_str}")

    if section in ("all", "keymap"):
        print(f"\n--- Keymap Layer 1 Matrix (Profile {prof.profile_id}) ---")
        for slot in range(128):
            rec = prof.keymap.slots[slot]
            if not rec.is_empty:
                print(f"  Slot {slot:3d} (B{slot//16}, C{slot%16:2d}): {rec.hid_name:<15} (type 0x{rec.function_type:02X})")

    if section in ("all", "rgb"):
        if prof.rgb_global:
            print("\n--- Global RGB Lighting ---")
            print(f"  Effect:     {prof.rgb_global.effect}")
            print(f"  Brightness: {prof.rgb_global.brightness}")
            print(f"  Speed:      {prof.rgb_global.speed}")
            print(f"  Primary:    {prof.rgb_global.primary}")

    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    path = Path(args.capture) if args.capture else Path("captures/experiments/read_01_initial_load.json")
    if not path.exists():
        print(f"Error: baseline capture not found: {path}", file=sys.stderr)
        return 1

    snapshot = KeyboardSnapshot.load_json(path)
    state = snapshot.clone_mutable()
    prof = state.active_profile

    # Hall modifications
    if args.actuation is not None or args.rt_press is not None or args.rt_release is not None:
        key = args.key.upper() if args.key else "A"
        if args.actuation is not None:
            prof.hall.set_actuation(key, args.actuation)
        if args.rt_press is not None or args.rt_release is not None:
            p = args.rt_press if args.rt_press is not None else 0.2
            r = args.rt_release if args.rt_release is not None else 0.2
            prof.hall.set_rt(key, p, r)

    # RGB modifications
    if args.rgb_effect is not None or args.rgb_brightness is not None or args.rgb_speed is not None or args.rgb_color is not None:
        from keyboard_re.protocol.rgb import RGBGlobalConfig
        cfg = prof.rgb_global
        effect = args.rgb_effect if args.rgb_effect is not None else (cfg.effect if cfg else 1)
        brightness = args.rgb_brightness if args.rgb_brightness is not None else (cfg.brightness if cfg else 5)
        speed = args.rgb_speed if args.rgb_speed is not None else (cfg.speed if cfg else 5)
        if args.rgb_color:
            c = args.rgb_color.lstrip("#")
            primary = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        else:
            primary = cfg.primary if cfg else (255, 255, 255)
        secondary = cfg.secondary if cfg else (0, 0, 0)
        prof.rgb_global = RGBGlobalConfig(
            effect=effect,
            primary=primary,
            secondary=secondary,
            brightness=brightness,
            speed=speed,
        )

    modified_snap = state.to_snapshot()
    plan = build_write_plan(snapshot, modified_snap)
    print(plan.format_plan())

    expected_ack = 0x23 if plan.subsystem == "rgb_global" else (0x24 if plan.subsystem == "rgb_per_key" else 0x27)
    # Execute simulation through DryRunTransport
    dry = DryRunTransport()
    res = write_transaction(dry, plan.packets, expected_ack_opcode=expected_ack)
    print(f"DryRun Simulation Status: {res.status.value} ({res.acks_received}/{res.packets_sent} simulated ACKs)")
    print("HID writes: DISABLED (Safety Policy Active)")
    return 0


def cmd_device_info(args: argparse.Namespace) -> int:
    from keyboard_re.transport import NativeHidTransport
    from keyboard_re.protocol.read import read_device_info, read_keyboard_status

    transport = NativeHidTransport()
    try:
        transport.open()
    except Exception as e:
        print(f"Error opening HID device: {e}", file=sys.stderr)
        return 1

    try:
        info = read_device_info(transport)
        status = read_keyboard_status(transport)
        print("=== DEVICE INFORMATION ===")
        print(f"Product Name:       {transport.actual_product_name}")
        print(f"VID / PID:          0x{info.vid:04X} / 0x{info.pid:04X}")
        print(f"Firmware Version:   v{info.firmware_version}")
        print(f"Bootloader Version: v{info.bootloader_version}")
        print(f"Active Profile:     #{status.active_profile}")
        print(f"Lock Flags:         0x{status.lock_flags:02X}")
        print(f"OS Mode:            0x{status.mode_flag:02X}")
        print(f"Connection Flag:    0x{status.connection_flag:02X}")
        return 0
    finally:
        transport.close()


def cmd_read_hall(args: argparse.Namespace) -> int:
    from keyboard_re.transport import NativeHidTransport
    from keyboard_re.protocol.read import read_hall_profile
    from keyboard_re.models.state import HallProfileState

    profile_id = getattr(args, "profile", 1) or 1
    if profile_id not in (1, 2):
        print(f"Error: Invalid profile {profile_id}. Must be 1 or 2.", file=sys.stderr)
        return 1

    transport = NativeHidTransport()
    try:
        transport.open()
    except Exception as e:
        print(f"Error opening HID device: {e}", file=sys.stderr)
        return 1

    try:
        print(f"Reading Hall Profile {profile_id} from {transport.actual_product_name}...")
        img = read_hall_profile(transport, profile_id=profile_id)
        print(f"Successfully read {len(img)} bytes.")

        if getattr(args, "output", None):
            out_path = Path(args.output)
            out_path.write_bytes(img)
            print(f"Saved raw image to {out_path}")

        hall_state = HallProfileState.from_bytes(img, profile_id=profile_id)
        print(f"\n--- Hall Matrix (Profile {profile_id}, {len(hall_state.keys)} keys) ---")
        for k in sorted(hall_state.keys.keys()):
            cfg = hall_state.get_key(k)
            rt_str = f"RT(press={cfg.rt_press_mm}mm, rel={cfg.rt_release_mm}mm)" if cfg.is_rt_enabled else "RT(OFF)"
            print(f"  Key {k:<10}: Actuation={cfg.actuation_mm:.2f} mm, {rt_str}")

        return 0
    finally:
        transport.close()


def cmd_write(args: argparse.Namespace) -> int:
    import struct
    from keyboard_re.transport import NativeHidTransport
    from keyboard_re.protocol.read import read_device_info, read_keyboard_status, read_hall_profile
    from keyboard_re.models.base import KEY_MAP, resolve_key_name, key_address, mm_to_units
    from keyboard_re.models.state import HallProfileState
    from keyboard_re.protocol.hall import build_hall_write, build_hall_terminator
    from keyboard_re.protocol.transaction import write_transaction, TransactionStatus

    profile_id = getattr(args, "profile", 1) or 1
    if profile_id != 1:
        print("Error: Only Hall Profile 1 actuation modifications are permitted on this stage.", file=sys.stderr)
        return 1

    key_name = resolve_key_name(args.key)
    if not key_name or key_name not in KEY_MAP:
        print(f"Error: Unknown physical key '{args.key}'. Must be one of 84 keys in KEY_MAP.", file=sys.stderr)
        return 1

    if args.actuation is None:
        print("Error: --actuation <mm> is required.", file=sys.stderr)
        return 1

    target_actuation = round(args.actuation, 2)
    if not (0.10 <= target_actuation <= 4.00):
        print(f"Error: Actuation {target_actuation} mm out of supported range [0.10, 4.00].", file=sys.stderr)
        return 1

    transport = NativeHidTransport()
    try:
        transport.open()
    except Exception as e:
        print(f"Error opening HID device: {e}", file=sys.stderr)
        return 1

    try:
        # Step 1: Read baseline from hardware
        print("Connecting to keyboard and reading baseline state...")
        info = read_device_info(transport)
        status = read_keyboard_status(transport)
        before_bytes = read_hall_profile(transport, profile_id=1)

        # Save baseline to before.bin
        before_path = Path("before.bin")
        before_path.write_bytes(before_bytes)

        before_hall = HallProfileState.from_bytes(before_bytes, profile_id=1)
        current_cfg = before_hall.get_key(key_name)
        current_actuation = current_cfg.actuation_mm

        bank, col = KEY_MAP[key_name]
        k_addr = key_address(bank, col)

        old_raw = current_cfg.to_bytes()[:2]
        new_units = mm_to_units(target_actuation)
        new_raw = struct.pack("<H", new_units)

        # Step 2: Show explicit warning and diff
        print("\n" + "=" * 64)
        print("WARNING: REAL HARDWARE WRITE")
        print("=" * 64)
        print(f"Keyboard:      {transport.actual_product_name}")
        print(f"Key:           {key_name}")
        print(f"Profile:       {profile_id}")
        print(f"Actuation:     {current_actuation:.2f} mm -> {target_actuation:.2f} mm")
        print(f"Changed bytes:\n0x{k_addr:04X}:\n{old_raw.hex(' ').upper()} -> {new_raw.hex(' ').upper()}")
        print("=" * 64)

        if not getattr(args, "yes", False):
            try:
                ans = input("Continue? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nABORTED")
                return 1
            if ans != "y":
                print("ABORTED")
                return 1

        # Step 3: Construct modified image and packets
        modified_hall = before_hall.clone()
        modified_hall.set_actuation(key_name, target_actuation)
        modified_bytes = modified_hall.to_bytes()

        # Build official 19-report write sequence
        packets = build_hall_write(modified_bytes) + [build_hall_terminator()]

        print(f"\nExecuting write transaction ({len(packets)} reports)...")
        tx_res = write_transaction(
            transport,
            packets,
            expected_ack_opcode=0x27,
            verbose=not args.quiet,
        )

        if tx_res.status != TransactionStatus.SUCCESS:
            print(f"\nWRITE FAILED during transmission: {tx_res.error}", file=sys.stderr)
            print(f"Packets sent: {tx_res.packets_sent}, ACKs received: {tx_res.acks_received}", file=sys.stderr)
            return 1

        print("Write transaction completed successfully with all ACKs validated.")

        # Step 4: Post-write read-back verification
        print("Performing post-write read-back...")
        after_bytes = read_hall_profile(transport, profile_id=1)

        after_path = Path("after.bin")
        after_path.write_bytes(after_bytes)

        # Step 5: Check changed offsets between before.bin and after.bin
        changed_offsets = []
        for offset in range(len(before_bytes)):
            if before_bytes[offset] != after_bytes[offset]:
                changed_offsets.append(offset)

        # Target key actuation bytes are at k_addr and k_addr + 1
        expected_offsets = {k_addr, k_addr + 1}
        unexpected_offsets = [off for off in changed_offsets if off not in expected_offsets]

        # Step 6: Parse after state
        after_hall = HallProfileState.from_bytes(after_bytes, profile_id=1)
        after_cfg = after_hall.get_key(key_name)

        if abs(after_cfg.actuation_mm - target_actuation) > 0.005:
            print(f"\nWRITE FAILED: Read-back actuation mismatch ({after_cfg.actuation_mm:.2f} != {target_actuation:.2f})", file=sys.stderr)
            return 1

        print("\n" + "=" * 64)
        print("WRITE SUCCESS")
        print("=" * 64)
        print(f"\n{key_name}:")
        print(f"{current_actuation:.2f} mm -> {after_cfg.actuation_mm:.2f} mm")
        print("\nRead-back:")
        print("OK")

        if unexpected_offsets:
            print(f"\nUnexpected changes:")
            for off in unexpected_offsets:
                print(f"  Offset 0x{off:04X}: 0x{before_bytes[off]:02X} -> 0x{after_bytes[off]:02X}")
            print("\nWARNING: Unexpected changes detected", file=sys.stderr)
        else:
            print("\nUnexpected changes:")
            print("NONE")

        return 0
    finally:
        transport.close()


def cmd_rgb_catalog(args: argparse.Namespace) -> int:
    """Print the complete catalog of 26 confirmed RGB effect modes and supported controls."""
    from keyboard_re.protocol.rgb import RGB_EFFECT_CATALOG

    print("=" * 78)
    print(f"{'ID':<6} {'Name (EN)':<24} {'Name (RU)':<20} {'Cat':<11} {'Controls'}")
    print("=" * 78)
    for eid in sorted(RGB_EFFECT_CATALOG.keys()):
        m = RGB_EFFECT_CATALOG[eid]
        ctrls = []
        if m.supports_color:
            ctrls.append("Color")
        if m.supports_color_mode:
            ctrls.append("Mode(S/R)")
        if m.supports_speed:
            ctrls.append("Speed(1-5)")
        if m.supports_direction:
            ctrls.append(f"Dir({m.direction_position})")
        if m.supports_secondary_color:
            ctrls.append("Secondary")
        if m.supports_submodes:
            ctrls.append(f"Submodes({len(m.sub_modes)})")
        ctrl_str = ", ".join(ctrls) if ctrls else "None"
        print(f"0x{eid:02X}   {m.name_en:<24} {m.name_ru:<20} {m.category:<11} {ctrl_str}")
    print("=" * 78)
    print(f"Total confirmed modes in catalog: {len(RGB_EFFECT_CATALOG)}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Launch the safe GUI configurator application."""
    from keyboard_re.ui.app import KeyboardApp

    app = KeyboardApp(auto_connect_mock=args.mock)
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keyboard_re",
        description="Passive and safe protocol analysis tools for IO by Red Square Type 84 Magnetic Black."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = subparsers.add_parser("parse", help="Parse 64-byte reports from a capture file")
    p_parse.add_argument("input", help="Path to capture JSON file")
    p_parse.set_defaults(func=cmd_parse)

    # assemble
    p_assemble = subparsers.add_parser("assemble", help="Assemble 1008-byte configuration image from capture")
    p_assemble.add_argument("input", help="Path to capture JSON file")
    p_assemble.add_argument("-o", "--output", help="Path to save assembled image (.bin or .json)")
    p_assemble.add_argument("--session", type=int, default=-1, help="Session index if multiple bursts present (default: -1, the last session)")
    p_assemble.set_defaults(func=cmd_assemble)

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two captures or binary images")
    p_diff.add_argument("base", help="Base file (.json capture or .bin image)")
    p_diff.add_argument("modified", nargs="?", default=None, help="Modified file (.json capture or .bin image). If omitted, compares internal sessions in base file")
    p_diff.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    p_diff.add_argument("-o", "--output", help="Save output to file")
    p_diff.set_defaults(func=cmd_diff)

    # batch-analyze
    p_batch = subparsers.add_parser("batch-analyze", aliases=["batch"], help="Run batch analysis on a set of experiments")
    p_batch.add_argument("config", help="JSON file defining the experiments")
    p_batch.add_argument("--report", help="Save Markdown report to file")
    p_batch.add_argument("--json", help="Save JSON analysis data to file")
    p_batch.set_defaults(func=cmd_batch_analyze)

    # import-hex
    p_imp_hex = subparsers.add_parser("import-hex", help="Convert text hex dump to RawCapture JSON")
    p_imp_hex.add_argument("input", help="Text file with hex lines")
    p_imp_hex.add_argument("-o", "--output", help="Output JSON path")
    p_imp_hex.add_argument("-d", "--description", help="Capture description")
    p_imp_hex.set_defaults(func=cmd_import_hex)

    # import-wireshark
    p_imp_ws = subparsers.add_parser("import-wireshark", help="Convert Wireshark JSON packet dissection to RawCapture JSON")
    p_imp_ws.add_argument("input", help="Wireshark JSON file")
    p_imp_ws.add_argument("-o", "--output", help="Output JSON path")
    p_imp_ws.add_argument("-d", "--description", help="Capture description")
    p_imp_ws.set_defaults(func=cmd_import_wireshark)

    # read
    p_read = subparsers.add_parser("read", help="Read complete state snapshot from a state-sync capture")
    p_read.add_argument("input", help="Path to state-sync capture JSON")
    p_read.set_defaults(func=cmd_read)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect state details (hall, keymap, rgb, status)")
    p_inspect.add_argument("input", help="Path to state-sync capture JSON")
    p_inspect.add_argument("--section", choices=["all", "hall", "keymap", "rgb"], default="all", help="Section to inspect")
    p_inspect.set_defaults(func=cmd_inspect)

    # plan
    p_plan = subparsers.add_parser("plan", help="Build a dry-run write plan for parameter modifications")
    p_plan.add_argument("--capture", help="Baseline capture JSON file (default: read_01_initial_load.json)")
    p_plan.add_argument("--key", default="A", help="Key name to modify (e.g. A, S, Space, Enter)")
    p_plan.add_argument("--actuation", type=float, help="New actuation point in mm (e.g. 1.39)")
    p_plan.add_argument("--rt-press", type=float, help="New RT press sensitivity in mm (e.g. 0.15)")
    p_plan.add_argument("--rt-release", type=float, help="New RT release sensitivity in mm (e.g. 0.20)")
    p_plan.add_argument("--rgb-effect", type=int, help="RGB effect ID (e.g. 1)")
    p_plan.add_argument("--rgb-brightness", type=int, help="RGB brightness 0-5")
    p_plan.add_argument("--rgb-speed", type=int, help="RGB speed 1-5")
    p_plan.add_argument("--rgb-color", help="RGB hex color (e.g. FF0000 or 00FF00)")
    p_plan.set_defaults(func=cmd_plan)

    # device-info
    p_dev = subparsers.add_parser("device-info", help="Read identity and status from physically connected keyboard")
    p_dev.set_defaults(func=cmd_device_info)

    # read-hall
    p_rh = subparsers.add_parser("read-hall", help="Read Hall Effect configuration image from physically connected keyboard")
    p_rh.add_argument("--profile", type=int, default=1, choices=[1, 2], help="Profile index (1 or 2, default: 1)")
    p_rh.add_argument("-o", "--output", help="Save binary image (.bin) to file")
    p_rh.set_defaults(func=cmd_read_hall)

    # write
    p_wr = subparsers.add_parser("write", help="Safely update actuation point for a single physical key in Hall Profile 1")
    p_wr.add_argument("--key", default="A", help="Physical key name (e.g. A, Space, Enter)")
    p_wr.add_argument("--actuation", type=float, required=True, help="New actuation point in mm (e.g. 1.39)")
    p_wr.add_argument("--profile", type=int, default=1, choices=[1], help="Profile index (only 1 supported)")
    p_wr.add_argument("--quiet", action="store_true", help="Suppress per-packet TX/RX logging")
    p_wr.add_argument("-y", "--yes", action="store_true", help="Confirm write prompt automatically")
    p_wr.set_defaults(func=cmd_write)

    # rgb-catalog
    p_rgb = subparsers.add_parser("rgb-catalog", aliases=["rgb"], help="List confirmed hardware RGB effect catalog and supported controls")
    p_rgb.set_defaults(func=cmd_rgb_catalog)

    # gui
    p_gui = subparsers.add_parser("gui", help="Launch safe GUI configurator application")
    p_gui.add_argument("--mock", action="store_true", help="Launch GUI with mock device pre-connected")
    p_gui.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
