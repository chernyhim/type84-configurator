with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

# Find export statement at the end of layout-classic.js
idx = js.rfind('export{')
if idx != -1:
    print('Exports at', idx)
    print(js[idx:idx+1500].encode('ascii', errors='replace').decode('ascii'))
else:
    print('No export{ found at end')
