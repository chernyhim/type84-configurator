with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

print("=== LINES 349700 - 353000 ===")
print(js[349700:353000].encode('ascii', errors='replace').decode('ascii'))
