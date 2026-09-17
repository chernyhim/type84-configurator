with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

idx = js.find('vendorId:3141,productId:32982,supplierName:"io"')
print("rc at", idx)
print(js[idx:idx+2500].encode('ascii', errors='replace').decode('ascii'))
