import json

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    data = json.load(f)

events = data['events']
responses = {}
for e in events:
    if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith('55'):
        cmd = e['data_hex'][:4]
        responses.setdefault(cmd, []).append(e)

for cmd, resps in responses.items():
    print(f'Cmd {cmd}: {len(resps)} responses')
    for r in resps[:2]:
        idx = r['index']
        dh = r['data_hex'][:60]
        print(f'   #{idx} hex: {dh}...')
