import json
import struct

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    d1 = json.load(f)

with open('captures/experiments/read_03_reconnect.json', 'r') as f:
    d3 = json.load(f)

def get_buf_12(data):
    buf = bytearray(512)
    for e in data['events']:
        if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith('5512'):
            raw = bytes.fromhex(e['data_hex'])
            sz = raw[2]
            addr = struct.unpack_from('<H', raw, 3)[0]
            payload = raw[5:5+sz]
            buf[addr:addr+len(payload)] = payload
    return bytes(buf)

b1 = get_buf_12(d1)
b3 = get_buf_12(d3)

print(f"buf_12 in d1 == d3: {b1 == b3}")
