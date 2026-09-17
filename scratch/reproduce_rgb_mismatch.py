import copy
import sys
from keyboard_re.models.state import DeviceState, Profile
from keyboard_re.profile_manager import ProfileManager
from keyboard_re.protocol.plan import build_profile_write_plan
from keyboard_re.protocol.read import parse_rgb_global_read_response
from keyboard_re.models.rgb_editor import RGBGlobalEditor
from keyboard_re.protocol.rgb import EFFECT_STATIC, EFFECT_CUSTOM, EFFECT_RIPPLE_SPREAD, RGBGlobalConfig

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load real device initial state capture
base_state = DeviceState.load_json("captures/experiments/read_01_initial_load.json")
mgr = ProfileManager()

print("Base state RGB Global:")
print(" ", base_state.rgb_global)
print("  raw_hex:", base_state.dump_raw_buffers()["rgb_global"].hex())

# Let's check several realistic profile apply paths:

# PATH 1: Profile created from JSON (e.g. user profile file saved to disk and loaded)
# Suppose profile JSON has:
prof_json = {
    "profile_id": 1,
    "name": "Custom Profile",
    "rgb_global": {
        "effect": 15,
        "primary": [255, 255, 255],
        "brightness": 5,
        "speed": 5,
        # note: user didn't specify driver_setting or color_mode or secondary
    }
}
p1 = Profile.from_dict(prof_json)
plan1 = build_profile_write_plan(base_state, p1)
print("\n--- PATH 1: Profile.from_dict ---")
print("Plan steps:", [s.subsystem for s in plan1.steps])
diff1 = mgr.compare_profile_with_state(p1, base_state)
sub1 = diff1.get_subsystem("rgb_global")
if sub1:
    print(f"Diff vs base_state: {sub1.summary}")
    for d in sub1.details:
        print(f"  {d.parameter}: target={d.new_value} vs state={d.old_value}")

# PATH 2: Profile created via create_profile_from_state, user edits color with RGBGlobalEditor
p2 = mgr.create_profile_from_state(base_state, profile_id=1, name="Edited")
# User edits RGB Global in UI: e.g. changes effect or sets single color
ed = RGBGlobalEditor.from_config(p2.rgb_global)
ed.set_effect(EFFECT_STATIC)
ed.set_single_color((255, 0, 0))
p2.rgb_global = ed.build()

plan2 = build_profile_write_plan(base_state, p2)
step2 = plan2.get_step("rgb_global")
written_pkt = step2.chunks[0].packet
print("\n--- PATH 2: RGBGlobalEditor on Profile ---")
print(f"Written packet AA 23: {written_pkt[:24].hex(' ')}")

# Device executes AA 23 and then host reads back via AA 13:
# Real firmware response to AA 13: echoes written parameters, but byte 12 (driver_setting) is 0, magic (bytes 22-23) is 0
readback_pkt = bytearray(written_pkt)
readback_pkt[0:3] = b"\x55\x13\x10"
readback_pkt[12] = 0x00  # Firmware internal status
readback_pkt[22:24] = b"\x00\x00"

readback_cfg = parse_rgb_global_read_response(readback_pkt)
readback_state = copy.deepcopy(base_state)
readback_state.rgb_global = readback_cfg

diff2 = mgr.compare_profile_with_state(p2, readback_state)
sub2 = diff2.get_subsystem("rgb_global")
print(f"Post-write Readback Diff summary: {sub2.summary}")
for d in sub2.details:
    print(f"  {d.parameter}: target={d.new_value} vs readback={d.old_value}")

# PATH 3: Direct modification of profile.rgb_global (e.g. color_mode = 0)
p3 = mgr.create_profile_from_state(base_state, profile_id=1, name="DirectMod")
p3.rgb_global.color_mode = 0
plan3 = build_profile_write_plan(base_state, p3)
step3 = plan3.get_step("rgb_global")
written_pkt3 = step3.chunks[0].packet
print("\n--- PATH 3: Direct modification p3.rgb_global.color_mode = 0 ---")
print(f"Written packet AA 23: {written_pkt3[:24].hex(' ')}")
print(f"Byte 16 (color_mode in packet): {written_pkt3[16]}")
readback_pkt3 = bytearray(written_pkt3)
readback_pkt3[0:3] = b"\x55\x13\x10"
readback_pkt3[12] = 0x00
readback_pkt3[22:24] = b"\x00\x00"
readback_cfg3 = parse_rgb_global_read_response(readback_pkt3)
readback_state3 = copy.deepcopy(base_state)
readback_state3.rgb_global = readback_cfg3
diff3 = mgr.compare_profile_with_state(p3, readback_state3)
sub3 = diff3.get_subsystem("rgb_global")
print(f"Post-write Readback Diff summary: {sub3.summary}")
for d in sub3.details:
    print(f"  {d.parameter}: target={d.new_value} vs readback={d.old_value}")
