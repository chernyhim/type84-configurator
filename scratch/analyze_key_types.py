import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c, HID_NAMES

print("=== ALL NON-ZERO SLOTS IN LAYER 1 (55 12) ===")
types_l1 = {}
for slot in range(128):
    rec = buf_12[slot*4:slot*4+4]
    if rec != b'\x00\x00\x00\x00':
        b0, b1, b2, b3 = rec
        types_l1.setdefault((b0, b2, b3), []).append((slot, b1))

for k, v in types_l1.items():
    print(f"Header/Type (b0=0x{k[0]:02x}, b2=0x{k[1]:02x}, b3=0x{k[2]:02x}): {len(v)} keys")
    sample = [f"Slot {s}: 0x{b:02x}({HID_NAMES.get(b, '?')})" for s, b in v[:8]]
    print("   Sample:", ", ".join(sample))

print("\n=== ALL NON-ZERO SLOTS IN LAYER 2 / FN (55 16) ===")
types_l2 = {}
for slot in range(128):
    rec = buf_16[slot*4:slot*4+4]
    if rec != b'\x00\x00\x00\x00':
        b0, b1, b2, b3 = rec
        types_l2.setdefault((b0, b2, b3), []).append((slot, b1))

for k, v in types_l2.items():
    print(f"Header/Type (b0=0x{k[0]:02x}, b2=0x{k[1]:02x}, b3=0x{k[2]:02x}): {len(v)} keys")
    sample = [f"Slot {s}: 0x{b:02x}({HID_NAMES.get(b, '?')})" for s, b in v[:8]]
    print("   Sample:", ", ".join(sample))
