import json

with open('captures/experiments/read_01_initial_load.json') as f:
    data = json.load(f)

for e in data['events']:
    if e.get('direction') == 'DEVICE -> HOST' and e['data_hex'].startswith('5511'):
        raw = bytes.fromhex(e['data_hex'])
        print("Raw 55 11 packet:")
        print(" ".join(f"{b:02X}" for b in raw[:32]))
        
        # In ta, i.slice(8) takes from offset 8
        r = raw[8:]
        print("\nOffsets in r (raw[8:]):")
        fields = {
            1: "gameMode",
            2: "fnSwitch",
            3: "sleepTime",
            4: "keyDelay",
            5: "reportRate",
            6: "systemMode",
            7: "tftDisplayTime",
            8: "topDeadZone",
            9: "bottomDeadZone",
            11: "stabilityMode",
            14: "autoCalibration",
            15: "singleKeyWakeup",
            16: "pushButtonMode"
        }
        for idx, val in enumerate(r[:25]):
            field_name = fields.get(idx, "-")
            pkt_offset = 8 + idx
            print(f"r[{idx:2d}] (pkt offset {pkt_offset:2d}): 0x{val:02X} ({val:3d})  -> {field_name}")
        break
