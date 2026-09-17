import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES

print("=== COMPLETE REMAP LAYER 1 (55 12) REFERENCE ===")
for bank in range(8):
    print(f"\n--- BANK {bank} (Slots {bank*16}..{bank*16+15}, Offsets 0x{bank*64:03x}..0x{(bank+1)*64-1:03x}) ---")
    for col in range(16):
        slot = bank * 16 + col
        rec = buf_12[slot*4 : slot*4+4]
        b0, b1, b2, b3 = rec
        name = HID_NAMES.get(b1, f"0x{b1:02x}") if b1 != 0 else (HID_NAMES.get(b0, f"0x{b0:02x}") if b0 != 0 else "---")
        type_str = f"type=0x{b3:02x}" if b3 != 0 else "type=0x00"
        special_str = f"special=0x{b2:02x}" if b2 != 0 else ""
        print(f"Col {col:2d} (Slot {slot:3d}, Off 0x{slot*4:03x}): [{rec.hex(' ')}] -> {name:15s} {type_str:10s} {special_str}")
