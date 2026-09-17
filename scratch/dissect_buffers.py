import json
import struct

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    data = json.load(f)

def extract_buffer(cmd_hex_prefix, total_size):
    buf = bytearray(total_size)
    for e in data['events']:
        if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith(cmd_hex_prefix):
            raw = bytes.fromhex(e['data_hex'])
            sz = raw[2]
            addr = struct.unpack_from('<H', raw, 3)[0]
            payload = raw[5:5+sz]
            buf[addr:addr+len(payload)] = payload
    return bytes(buf)

# 1. Inspect Status (55 11)
for e in data['events']:
    if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith('5511'):
        raw = bytes.fromhex(e['data_hex'])
        print("=== STATUS 55 11 ===")
        print("Full hex:", raw.hex())
        print("Header [0..4]:", raw[:5].hex())
        print("Payload [5..21] (16 bytes):", raw[5:21].hex())
        print("Payload bytes:", [f"b{i}:0x{b:02x}" for i, b in enumerate(raw[5:21])])

# 2. Extract 55 12, 55 16, 55 1c
buf_12 = extract_buffer('5512', 512)
buf_16 = extract_buffer('5516', 512)
buf_1c = extract_buffer('551c', 512)

print("\n=== BUF 55 12 (Remap L1) ===")
print(f"Len: {len(buf_12)}")
# Let's inspect records. Is each record 4 bytes?
records_12 = [buf_12[i:i+4] for i in range(0, 512, 4)]
print(f"Records count (if 4 bytes): {len(records_12)}")
for i, r in enumerate(records_12[:25]):
    val = struct.unpack('<H', r[:2])[0]
    extra = struct.unpack('<H', r[2:4])[0]
    print(f"Slot {i:2d} (offset {i*4:3d}): raw={r.hex()} -> u16_0=0x{val:04x}, u16_1=0x{extra:04x}")

print("\n=== BUF 55 16 (Fn Layer) ===")
records_16 = [buf_16[i:i+4] for i in range(0, 512, 4)]
for i, r in enumerate(records_16[:25]):
    val = struct.unpack('<H', r[:2])[0]
    extra = struct.unpack('<H', r[2:4])[0]
    if r != b'\x00\x00\x00\x00':
        print(f"Slot {i:2d} (offset {i*4:3d}): raw={r.hex()} -> u16_0=0x{val:04x}, u16_1=0x{extra:04x}")

print("\n=== BUF 55 1c ===")
records_1c = [buf_1c[i:i+4] for i in range(0, 512, 4)]
for i, r in enumerate(records_1c[:25]):
    val = struct.unpack('<H', r[:2])[0]
    extra = struct.unpack('<H', r[2:4])[0]
    if r != b'\x00\x00\x00\x00':
        print(f"Slot {i:2d} (offset {i*4:3d}): raw={r.hex()} -> u16_0=0x{val:04x}, u16_1=0x{extra:04x}")
