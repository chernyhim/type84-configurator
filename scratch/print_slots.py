import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES

print("=== SLOTS 0 to 31 ===")
for slot in range(32):
    off = slot * 4
    rec = buf_12[off:off+4]
    b0, b1, b2, b3 = rec
    name = HID_NAMES.get(b1, f"0x{b1:02x}")
    bank = slot // 16
    col = slot % 16
    print(f"Slot {slot:2d} (B{bank}, C{col:2d}): {rec.hex(' ')} -> {name}")
