import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(r"C:\Users\Илья\.gemini\antigravity-ide\brain\cedfaaa9-8c65-4459-90fb-6f33d6e74157\.system_generated\logs\transcript_full.jsonl", "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if 2895 <= idx <= 2915:
            d = json.loads(line)
            step = d.get("step_index")
            print(f"=== Line {idx} Step {step} ===")
            content = d.get("content") or ""
            if content:
                print("CONTENT:", content[:800])
            if d.get("tool_calls"):
                print("TOOL_CALLS:", d.get("tool_calls"))
