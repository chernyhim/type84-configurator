with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

print("=== setGameMode calls ===")
for m in re.finditer(r'setGameMode', js):
    pos = m.start()
    print(f"\n--- at {pos} ---")
    print(js[max(0, pos-80):pos+180].encode('ascii', errors='replace').decode('ascii'))
