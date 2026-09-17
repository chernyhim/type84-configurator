import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log_path = r"C:\Users\Илья\.gemini\antigravity-ide\brain\cedfaaa9-8c65-4459-90fb-6f33d6e74157\.system_generated\logs\transcript_full.jsonl"
with open(log_path, "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if 9210 <= idx <= 9320:
            d = json.loads(line)
            step = d.get("step_index")
            mtype = d.get("type")
            print(f"=== Line {idx} (Step {step}) type={mtype} ===")
            if d.get("thinking"):
                print("THINKING:", d["thinking"][:300])
            if d.get("tool_calls"):
                print("TOOL_CALLS:", d["tool_calls"])
            if mtype == "RUN_COMMAND":
                print("OUTPUT:", d.get("content", "")[:300])
