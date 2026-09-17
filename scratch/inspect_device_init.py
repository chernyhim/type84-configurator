with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

idx = js.find('ru=')
print('ru= at', idx)
print(js[idx:idx+1200].encode('ascii', errors='replace').decode('ascii'))
