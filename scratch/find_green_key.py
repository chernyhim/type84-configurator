import json

with open("captures/experiments/rgb_05_per_key.json", "r") as f:
    data = json.load(f)
    reports = data.get("reports", [])
    for idx, r in enumerate(reports):
        raw = r.get("data_hex") or ""
        if raw.startswith("aa24"):
            b = bytes.fromhex(raw)
            # addr is at b[3:5]
            addr = b[3] | (b[4] << 8)
            sz = b[2]
            payload = b[5:5+sz]
            for slot_offset in range(0, len(payload), 4):
                chunk = payload[slot_offset:slot_offset+4]
                slot_id = (addr + slot_offset) // 4
                if chunk[:3] != b"\x00\x00\x00":
                    print(f"Report #{idx}: Slot {slot_id}: {chunk.hex()} -> R={chunk[0]}, G={chunk[1]}, B={chunk[2]}, LED_ID={chunk[3]}")
