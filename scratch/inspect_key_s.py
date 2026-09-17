import json

data = json.load(open("captures/key_s_139mm.json", "r", encoding="utf-8"))
for rep_idx in [7, 26]:
    raw = bytes.fromhex(data["reports"][rep_idx]["data_hex"])
    payload = raw[8 : 8 + 56]
    print(f"Rep #{rep_idx}:")
    for row in range(7):
        chunk = payload[row * 8 : (row + 1) * 8]
        addr = 392 + row * 8
        print(f"  {addr:4d}..{addr+7:4d}: {' '.join(f'{b:02X}' for b in chunk)}")
