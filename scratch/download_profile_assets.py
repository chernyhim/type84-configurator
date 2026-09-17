import urllib.request
import os

urls = [
    "https://web.io.vision/assets/profiles-DRc7-3Ol.js",
    "https://web.io.vision/assets/useProfiles-CdvILL1K.js"
]

for url in urls:
    fname = os.path.basename(url)
    target = os.path.join("scratch", fname)
    print(f"Downloading {url} to {target}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            with open(target, "wb") as f:
                f.write(data)
            print(f"  Success: {len(data)} bytes")
    except Exception as e:
        print(f"  Failed: {e}")
