with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

print("=== GAME MODE OCCURRENCES ===")
matches = [m.start() for m in re.finditer(r'gameMode', js)]
print(f"Total occurrences of gameMode: {len(matches)}")
for pos in matches[:15]:
    print(f"\n--- at {pos} ---")
    print(js[max(0, pos-60):pos+180].encode('ascii', errors='replace').decode('ascii'))
