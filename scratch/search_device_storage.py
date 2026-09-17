with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

# Search for deviceStorage properties
print("=== deviceStorage properties ===")
matches = re.findall(r'deviceStorage\.([a-zA-Z0-9_]+)', js)
print("deviceStorage keys:", set(matches))

# Search for any occurrence of 'currentProfileIndex'
print("\n=== currentProfileIndex ===")
matches = [m.start() for m in re.finditer(r'currentProfileIndex', js)]
print(f"Occurrences: {len(matches)}")
for pos in matches[:10]:
    print(js[max(0, pos-40):pos+100].encode('ascii', errors='replace').decode('ascii'))
