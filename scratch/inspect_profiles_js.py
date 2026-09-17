import os

for fname in ["useProfiles-CdvILL1K.js", "profiles-DRc7-3Ol.js"]:
    path = os.path.join("scratch", fname)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    print(f"=== {fname} ({len(content)} chars) ===")
    print(content[:3000].encode("ascii", errors="replace").decode("ascii"))
    if len(content) > 3000:
        print("\n--- part 2 ---")
        print(content[3000:6000].encode("ascii", errors="replace").decode("ascii"))
    print("\n" + "="*50 + "\n")
