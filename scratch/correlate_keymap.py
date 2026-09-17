import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES
from keyboard_re.models import KEY_MAP

print("=== CORRELATING KEY_MAP WITH BUF_12 ===")
# In KEY_MAP, each key -> (bank, column)
# Banks 0..6
# In buf_12, 512 bytes = 8 banks of 16 slots (4 bytes per slot)
# Let's check for each key in KEY_MAP:
# Does slot = bank * 16 + column match the key?
matches = []
mismatches = []
empty_slots = []

for key, (bank, col) in sorted(KEY_MAP.items(), key=lambda x: (x[1][0], x[1][1])):
    slot = bank * 16 + col
    rec = buf_12[slot*4 : slot*4+4]
    b0, b1, b2, b3 = rec
    hid_name = HID_NAMES.get(b1, f"0x{b1:02x}")
    print(f"Key {key:12s} in KEY_MAP is (B{bank}, C{col:2d}) -> Slot {slot:3d}: rec={rec.hex(' ')} | HID={hid_name}")
