import json
import struct

HID_NAMES = {
    0x00: "None",
    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D", 0x08: "E", 0x09: "F",
    0x0A: "G", 0x0B: "H", 0x0C: "I", 0x0D: "J", 0x0E: "K", 0x0F: "L",
    0x10: "M", 0x11: "N", 0x12: "O", 0x13: "P", 0x14: "Q", 0x15: "R",
    0x16: "S", 0x17: "T", 0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z",
    0x1E: "1!", 0x1F: "2@", 0x20: "3#", 0x21: "4$", 0x22: "5%",
    0x23: "6^", 0x24: "7&", 0x25: "8*", 0x26: "9(", 0x27: "0)",
    0x28: "Enter", 0x29: "Escape", 0x2A: "Backspace", 0x2B: "Tab",
    0x2C: "Space", 0x2D: "-_", 0x2E: "=+", 0x2F: "[{", 0x30: "]}",
    0x31: "\\|", 0x32: "NonUS#", 0x33: ";:", 0x34: "'\"", 0x35: "`~",
    0x36: ",<", 0x37: ".>", 0x38: "/?", 0x39: "CapsLock",
    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4", 0x3E: "F5", 0x3F: "F6",
    0x40: "F7", 0x41: "F8", 0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",
    0x46: "PrintScreen", 0x47: "ScrollLock", 0x48: "Pause",
    0x49: "Insert", 0x4A: "Home", 0x4B: "PageUp", 0x4C: "Delete",
    0x4D: "End", 0x4E: "PageDown", 0x4F: "RightArrow", 0x50: "LeftArrow",
    0x51: "DownArrow", 0x52: "UpArrow",
    0x53: "NumLock", 0x54: "KPSlash", 0x55: "KPAsterisk", 0x56: "KPMinus",
    0x57: "KPPlus", 0x58: "KPEnter", 0x59: "KP1", 0x5A: "KP2", 0x5B: "KP3",
    0x5C: "KP4", 0x5D: "KP5", 0x5E: "KP6", 0x5F: "KP7", 0x60: "KP8",
    0x61: "KP9", 0x62: "KP0", 0x63: "KPDot", 0x64: "NonUSBackslash",
    0x65: "Application/Menu",
    0xAF: "Fn",
    0xE0: "LCtrl", 0xE1: "LShift", 0xE2: "LAlt", 0xE3: "LGui",
    0xE4: "RCtrl", 0xE5: "RShift", 0xE6: "RAlt", 0xE7: "RGui",
}

with open('captures/experiments/read_01_initial_load.json', 'r') as f:
    data = json.load(f)

def extract_buffer(cmd_hex_prefix, total_size):
    buf = bytearray(total_size)
    for e in data['events']:
        if e['direction'] == 'DEVICE -> HOST' and e['data_hex'].startswith(cmd_hex_prefix):
            raw = bytes.fromhex(e['data_hex'])
            sz = raw[2]
            addr = struct.unpack_from('<H', raw, 3)[0]
            payload = raw[5:5+sz]
            buf[addr:addr+len(payload)] = payload
    return bytes(buf)

buf_12 = extract_buffer('5512', 512)
buf_16 = extract_buffer('5516', 512)
buf_1c = extract_buffer('551c', 512)

print("--- BANK 0 ---")
for col in range(16):
    idx = col
    offset = idx * 4
    rec = buf_12[offset:offset+4]
    b0, b1, b2, b3 = rec
    name = HID_NAMES.get(b1, f"0x{b1:02x}") if b1 != 0 else (HID_NAMES.get(b0, f"0x{b0:02x}") if b0 != 0 else "---")
    print(f"Col {col:2d}: {rec.hex(' ')} -> {name}")
