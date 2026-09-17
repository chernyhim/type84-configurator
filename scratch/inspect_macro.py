import sys
from pathlib import Path
import struct
import json

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    data = json.load(f)

print("=== MACRO / DKS (AA 15 -> 55 15) ===")
macro_reports = []
for e in data['events']:
    if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith('5515'):
        raw = bytes.fromhex(e['data_hex'])
        sz = raw[2]
        addr = struct.unpack_from('<H', raw, 3)[0]
        payload = raw[5:5+sz]
        print(f"Report #{e['index']:3d}: sz={sz} addr=0x{addr:04x} payload_hex={payload[:16].hex(' ')}... non_zero={sum(1 for b in payload if b != 0)}")
        macro_reports.append((addr, payload))

total_buf = bytearray(400)
for addr, p in macro_reports:
    total_buf[addr:addr+len(p)] = p

print(f"Total buffer size: {len(total_buf)}")
print(f"Total non-zero bytes in macro buffer: {sum(1 for b in total_buf if b != 0)}")
if sum(1 for b in total_buf if b != 0) > 0:
    for i in range(0, len(total_buf), 16):
        chunk = total_buf[i:i+16]
        if any(b != 0 for b in chunk):
            print(f"Offset 0x{i:03x}: {chunk.hex(' ')}")
