with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

import re

for term in ['80d6', '80D6', '32982', '0c45', '0C45', '3141', 'Type 84', 'Type84']:
    matches = [m.start() for m in re.finditer(term, js, re.IGNORECASE)]
    print(f"Term '{term}': {len(matches)} matches")
    for pos in matches[:5]:
        print(f"  at {pos}: " + js[max(0, pos-40):pos+120].encode('ascii', errors='replace').decode('ascii'))
