import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from analyze_keymap_details import buf_12, buf_16, buf_1c

targets = {
    0x2c: "Space",
    0x2d: "Minus",
    0x12: "O",
    0x0d: "J",
    0x05: "B",
}

print("=== SEARCHING MISSING SCANCODES IN BUF_12 ===")
for scancode, name in targets.items():
    found = []
    for i, b in enumerate(buf_12):
        if b == scancode:
            found.append((i, i // 4, (i // 4) // 16, (i // 4) % 16, i % 4))
    print(f"Scancode 0x{scancode:02x} ({name}): found {len(found)} times")
    for f_item in found:
        byte_off, slot, bank, col, b_idx = f_item
        rec = buf_12[slot*4:slot*4+4]
        print(f"   Byte {byte_off} (Slot {slot}, B{bank} C{col}, byte {b_idx}): raw={rec.hex(' ')}")
