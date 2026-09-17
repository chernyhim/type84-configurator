"""
Manual Physical Smoke Test for Visualizer Hardware Output.

Enables safe, standalone verification on physical hardware:
1. Connects to the physical keyboard via NativeHidTransport.
2. Reads and saves current Per-Key RGB state (AA14).
3. Transmits 3 distinct, easily verifiable static patterns:
   - Pattern 1: All RED keys (255, 0, 0)
   - Pattern 2: All BLUE keys (0, 0, 255)
   - Pattern 3: Row-by-Row Rainbow (Yellow, Green, Cyan, Magenta, Orange, White)
4. Restores initial Per-Key RGB state cleanly upon completion or interruption.

Strict safety:
- Does NOT execute any non-RGB opcodes (NO AA21, AA22, AA25, AA26, AA27, AA28).
- Does NOT run automatically in unit tests.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Optional, Tuple

from keyboard_re.protocol.transport import HidTransport
from keyboard_re.ui.layout_data import TYPE84_LAYOUT
from keyboard_re.visualizer.frame import RGBFrame
from keyboard_re.visualizer.output import HardwareOutputError, KeyboardRgbOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("visualizer.smoke_test")

# Distinct color palette per physical row
ROW_COLORS = {
    0: (255, 255, 0),    # Row 0 (F-keys): Yellow
    1: (0, 255, 0),      # Row 1 (Numbers): Green
    2: (0, 255, 255),    # Row 2 (QWERTY): Cyan
    3: (255, 0, 255),    # Row 3 (ASDF): Magenta
    4: (255, 128, 0),    # Row 4 (ZXCV): Orange
    5: (255, 255, 255),  # Row 5 (Modifiers/Space): White
}


def build_test_patterns() -> List[Tuple[str, RGBFrame]]:
    """Build the 3 manual smoke test patterns."""
    # Pattern 1: Solid Red
    red_frame = RGBFrame.solid((255, 0, 0))

    # Pattern 2: Solid Blue
    blue_frame = RGBFrame.solid((0, 0, 255))

    # Pattern 3: Row-by-row rainbow
    rainbow_frame = RGBFrame()
    for key_def in TYPE84_LAYOUT:
        color = ROW_COLORS.get(key_def.row, (255, 255, 255))
        rainbow_frame.set_color(key_def.led_slot, color)

    return [
        ("Solid Red (All 84 Keys)", red_frame),
        ("Solid Blue (All 84 Keys)", blue_frame),
        ("Row-by-Row Rainbow (Yellow, Green, Cyan, Magenta, Orange, White)", rainbow_frame),
    ]


def run_hardware_smoke_test(
    transport: Optional[HidTransport] = None,
    step_delay: float = 2.5,
) -> bool:
    """
    Execute manual hardware smoke test sequence on physical keyboard.

    Returns True if test completed successfully and baseline was restored.
    """
    # 1. Connect transport if not provided
    if transport is None:
        try:
            from keyboard_re.transport.native_hid import NativeHidTransport
            transport = NativeHidTransport()
            transport.open()
        except Exception as exc:
            logger.error("Failed to connect to physical keyboard: %s", exc)
            return False

    from keyboard_re.visualizer.telemetry import VisualizerTelemetry
    telem = VisualizerTelemetry(enabled=True)
    output = KeyboardRgbOutput(transport=transport, timeout=1.5, strict_ack=True, telemetry=telem)

    print("\n" + "=" * 60)
    print("VISUALIZER HARDWARE SMOKE TEST")
    print("=" * 60)
    print("1. Reading baseline Per-Key RGB (AA14) and Global RGB (AA13)...")

    try:
        output.start()
        print(f"   Baseline Per-Key RGB saved: {len(output.saved_rgb_buffer or b'')} bytes.")
        global_eff = output.saved_rgb_global.effect if output.saved_rgb_global else 0
        print(f"   Baseline Global RGB saved: effect=0x{global_eff:02X}.")
        print("   Switched Global RGB to Custom Per-Key mode (0x80).")

        import hashlib
        patterns = build_test_patterns()
        for idx, (name, frame) in enumerate(patterns, 1):
            buf = frame.to_led_buffer()
            buf_sha = hashlib.sha256(buf).hexdigest()
            first_32_hex = buf[:32].hex(" ").upper()

            print(f"\n2.{idx} Sending Pattern: {name}")
            output.write_frame(frame)
            print(f"     Frame written: frames_written={output.frames_written}, chunks=10 AA24")
            print(f"     Buffer SHA-256: {buf_sha}")
            print(f"     First 32 bytes: {first_32_hex}")
            print(f"     Holding for {step_delay:.1f}s...")
            time.sleep(step_delay)

        print("\n3. Test patterns complete. Restoring initial Per-Key RGB and Global RGB states...")
        output.stop(restore=True)
        print("   Baseline Per-Key RGB and Global RGB successfully restored!")
        print("\n" + "=" * 60)
        print("TEST RESULT: PASS (All patterns rendered and restored)")
        print("=" * 60 + "\n")
        return True

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user. Restoring initial Per-Key RGB and Global RGB states...")
        try:
            output.stop(restore=True)
            print("   Baseline states successfully restored!")
        except Exception as e:
            print(f"   [!] Error during restore: {e}")
        return False

    except Exception as exc:
        print(f"\n[!] Error during smoke test: {exc}")
        print("   Attempting emergency state restoration...")
        try:
            output.stop(restore=True)
            print("   Baseline states successfully restored!")
        except Exception as e:
            print(f"   [!] Error during emergency restore: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualizer Hardware Output Manual Smoke Test")
    parser.add_argument(
        "--step-delay",
        type=float,
        default=2.5,
        help="Duration in seconds to hold each test pattern (default: 2.5)",
    )
    args = parser.parse_args()

    success = run_hardware_smoke_test(step_delay=args.step_delay)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
