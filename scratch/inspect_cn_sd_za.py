with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

for name in ['cn', 'sd', 'za']:
    print(f"\n==================== {name} ====================")
    # search for 'const name=' or 'let name=' or 'var name=' or 'function name' or 'name='
    pos = 0
    while True:
        idx = js.find(f'{name}=', pos)
        if idx == -1:
            break
        print(f'Match at {idx}:')
        print(js[max(0, idx-50):idx+800].encode('ascii', errors='replace').decode('ascii'))
        pos = idx + len(name) + 1
        if pos > idx + 2000:
            break
