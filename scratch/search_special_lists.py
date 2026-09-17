with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

for term in ['funcList', 'specialList', 'fnFuncIds']:
    idx = js.find(f'{term}:')
    if idx != -1:
        print(f"\n=== {term} ===")
        print(js[idx:idx+400].encode('ascii', errors='replace').decode('ascii'))
