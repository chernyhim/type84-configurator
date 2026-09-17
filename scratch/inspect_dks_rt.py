with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

for name in ['Jo', 'Uu', 'Xo', 'Pu']:
    idx = js.find(f'{name}=')
    if idx != -1:
        print(f"\n=== {name} ===")
        print(js[idx:idx+600].encode('ascii', errors='replace').decode('ascii'))
