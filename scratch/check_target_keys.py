import json
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES
from keyboard_re.models import KEY_MAP

target_keys = ["ESC", "A", "Q", "SPACE", "ENTER", "LEFT", "UP", "BACKSPACE"]

print("=== CHECKING TARGET KEYS MAPPING ===")
print(f"{'Key':10s} | {'Hall (B, C)':12s} | {'Hall Addr':10s} | {'Remap Slot':11s} | {'Remap (B, C)':13s} | {'Remap Bytes':14s} | {'HID Usage':12s}")
print("-" * 90)

for k in target_keys:
    hall_b, hall_c = KEY_MAP[k]
    hall_addr = hall_b * 128 + 5 + hall_c * 8
    
    # Let's search where this key is in buf_12!
    # Does it exist at (hall_b, hall_c + 1)?
    expected_slot = hall_b * 16 + (hall_c + 1)
    rec_expected = buf_12[expected_slot*4 : expected_slot*4+4]
    
    # Also find all slots in buf_12 where the standard HID usage for this key appears:
    # Let's find the HID code for this key:
    hid_code = None
    for code, name in HID_NAMES.items():
        if name.upper() == k or (k == "ESC" and name == "Escape") or (k == "SPACE" and name == "Space") or (k == "ENTER" and name == "Enter") or (k == "LEFT" and name == "LeftArrow") or (k == "UP" and name == "UpArrow") or (k == "BACKSPACE" and name == "Backspace"):
            hid_code = code
            break
            
    # Search buf_12 for hid_code
    actual_slots = []
    if hid_code is not None:
        for s in range(128):
            if buf_12[s*4 + 1] == hid_code and buf_12[s*4 + 3] in (0x02, 0x03):
                actual_slots.append(s)
                
    actual_str = ", ".join([f"Slot {s} (B{s//16}, C{s%16})" for s in actual_slots])
    rec_hex = rec_expected.hex(' ')
    hid_name = HID_NAMES.get(rec_expected[1], f"0x{rec_expected[1]:02x}")
    print(f"{k:10s} | B{hall_b}, C{hall_c:2d}     | 0x{hall_addr:04X} ({hall_addr:4d}) | Slot {expected_slot:3d}    | B{expected_slot//16}, C{expected_slot%16:2d}      | {rec_hex:14s} | {hid_name:12s} | Actual matches: {actual_str}")
