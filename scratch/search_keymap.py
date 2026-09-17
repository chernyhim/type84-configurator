import json
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES

print("=== SEARCHING ALL BYTES IN BUF_12 ===")
for offset in range(0, 512, 4):
    rec = buf_12[offset:offset+4]
    b0, b1, b2, b3 = rec
    bank = offset // 64
    col = (offset % 64) // 4
    slot = offset // 4
    # let's print whenever any byte is non-zero
    if rec != b'\x00\x00\x00\x00':
        scancode = b1
        type_flag = b3
        name = HID_NAMES.get(scancode, f"0x{scancode:02x}")
        print(f"Slot {slot:3d} (Bank {bank}, Col {col:2d}, Off 0x{offset:03x}): raw={rec.hex(' ')} | code=0x{scancode:02x} ({name:15s}) flags=b0:0x{b0:02x}, b2:0x{b2:02x}, b3:0x{b3:02x}")
