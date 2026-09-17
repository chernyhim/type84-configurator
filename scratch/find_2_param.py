import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(r"C:\Users\Илья\.gemini\antigravity-ide\brain\cedfaaa9-8c65-4459-90fb-6f33d6e74157\.system_generated\logs\transcript_full.jsonl", "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if "2 parameter" in line:
            d = json.loads(line)
            step = d.get("step_index")
            mtype = d.get("type")
            print(f"Line {idx} Step {step} ({mtype}):")
            c = (d.get("content") or "") + "\n" + (d.get("thinking") or "")
            for sub in c.split("\n"):
                if "2 parameter" in sub:
                    print("  ", sub[:150])
