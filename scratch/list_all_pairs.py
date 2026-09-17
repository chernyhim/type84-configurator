import json

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    data = json.load(f)

events = data['events']
print(f"Total events: {len(events)}")
for i, e in enumerate(events):
    if e['direction'] == 'HOST -> DEVICE':
        resp = events[i+1] if i+1 < len(events) else None
        req_hex = e['data_hex'][:20]
        resp_hex = resp['data_hex'][:20] if resp else "NONE"
        resp_len = resp['length'] if resp else 0
        cmd = e['data_hex'][:4]
        addr = e['data_hex'][6:10]
        chunk = e['data_hex'][4:6]
        print(f"Req #{e['index']:3d} ({cmd} sz={chunk} addr={addr}) -> Resp #{resp['index']:3d} ({resp_hex[:8]} len={resp_len})")
