import json

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    d1 = json.load(f)

with open('captures/experiments/read_03_reconnect.json', 'r') as f:
    d3 = json.load(f)

resp1 = bytes.fromhex(d1['events'][4]['data_hex'])
resp3 = bytes.fromhex(d3['events'][4]['data_hex'])

print("=== STATUS 55 11 COMPARISON ===")
print("Capture 1 (initial):", resp1[:32].hex(' '))
print("Capture 3 (reconnect):", resp3[:32].hex(' '))
print(f"Equal? {resp1 == resp3}")

# Breakdown of bytes:
print("\nDetailed bytes breakdown:")
for i in range(len(resp1[:24])):
    b = resp1[i]
    print(f"Offset {i:2d} (0x{i:02x}): 0x{b:02x} ({b:3d})")
