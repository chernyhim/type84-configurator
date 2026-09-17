import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for f in glob.glob("captures/**/*.json", recursive=True):
    try:
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        events = d.get("events", [])
        found = []
        for e in events:
            data = e.get("data_hex", "").lower()
            if any(data.startswith(p) for p in ["aa23", "5523", "aa13", "5513"]):
                found.append((e.get("direction"), data[:48]))
        if found:
            print(f"=== {f} ({len(found)} events) ===")
            for direction, data in found[:10]:
                print(f"  {direction}: {data}")
    except Exception as exc:
        pass
