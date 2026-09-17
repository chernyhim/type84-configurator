with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

print("=== cn in full ===")
print(js[352250:353500].encode('ascii', errors='replace').decode('ascii'))
