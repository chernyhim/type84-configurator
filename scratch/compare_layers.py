import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES

print("=== COMPARING BUF_12, BUF_16, BUF_1C ===")
print(f"buf_12 == buf_16: {buf_12 == buf_16}")
print(f"buf_12 == buf_1c: {buf_12 == buf_1c}")
print(f"buf_16 == buf_1c: {buf_16 == buf_1c}")

diffs_12_16 = []
for slot in range(128):
    rec12 = buf_12[slot*4:slot*4+4]
    rec16 = buf_16[slot*4:slot*4+4]
    rec1c = buf_1c[slot*4:slot*4+4]
    if rec12 != rec16 or rec12 != rec1c:
        name12 = HID_NAMES.get(rec12[1], f"0x{rec12[1]:02x}")
        name16 = HID_NAMES.get(rec16[1], f"0x{rec16[1]:02x}")
        name1c = HID_NAMES.get(rec1c[1], f"0x{rec1c[1]:02x}")
        print(f"Slot {slot:3d} (Bank {slot//16}, Col {slot%16:2d}):")
        print(f"   L1 (12): {rec12.hex(' ')} ({name12})")
        print(f"   L2 (16): {rec16.hex(' ')} ({name16})")
        print(f"   L3 (1c): {rec1c.hex(' ')} ({name1c})")
