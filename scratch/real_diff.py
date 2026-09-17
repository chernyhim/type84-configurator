import json
from keyboard_re.parser import parse_capture
from keyboard_re.assembler import assemble_packets

data = json.load(open("captures/key_s_139mm.json", "r", encoding="utf-8"))
raw_reps = [r["data_hex"] for r in data["reports"]]
# 18 reports per session
rep1 = [bytes.fromhex(h) for h in raw_reps[:18]]
rep2 = [bytes.fromhex(h) for h in raw_reps[18:36]]

buf1 = bytearray(1008)
buf2 = bytearray(1008)

for r in rep1:
    addr = r[3] | (r[4] << 8)
    buf1[addr : addr + r[2]] = r[8 : 8 + r[2]]

for r in rep2:
    addr = r[3] | (r[4] << 8)
    buf2[addr : addr + r[2]] = r[8 : 8 + r[2]]

diffs = []
for i in range(1008):
    if buf1[i] != buf2[i]:
        diffs.append((i, buf1[i], buf2[i]))

print("Real diffs between session 1 and 2:")
for addr, b1, b2 in diffs:
    slot = addr // 8
    off = addr % 8
    print(f"  Addr {addr} (0x{addr:04X}): slot {slot}, offset +{off}: 0x{b1:02X} ({b1}) -> 0x{b2:02X} ({b2})")
